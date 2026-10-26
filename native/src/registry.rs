use regex::Regex;
use serde_json::{json, Value};
use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};

const IDENTIFIER: &str = r"[A-Za-z][A-Za-z0-9_$#]*";
const SOURCE_EXTENSIONS: &[&str] = &[".pkb", ".pks", ".pkg", ".sql"];
const IGNORED_DIRECTORIES: &[&str] = &[".git", ".venv", "__pycache__"];

#[derive(Default)]
struct LineIndex {
    newlines: Vec<usize>,
    length: usize,
}

impl LineIndex {
    fn new(source: &str) -> Self {
        let mut index = Self::default();
        for (offset, character) in source.chars().enumerate() {
            if character == '\n' {
                index.newlines.push(offset);
            }
            index.length = offset + 1;
        }
        index
    }

    fn line_at(&self, offset: usize) -> usize {
        self.newlines.partition_point(|line| *line < offset) + 1
    }
}

#[derive(Default)]
struct CharMap {
    byte_offsets: Vec<usize>,
}

impl CharMap {
    fn new(value: &str) -> Self {
        let mut byte_offsets: Vec<usize> = value.char_indices().map(|(byte, _)| byte).collect();
        byte_offsets.push(value.len());
        Self { byte_offsets }
    }

    fn char_at(&self, byte: usize) -> usize {
        self.byte_offsets
            .binary_search(&byte)
            .expect("regular expression offsets are character boundaries")
    }
}

fn regex(pattern: &str) -> Regex {
    Regex::new(pattern).expect("static regex is valid")
}

fn chars_slice(source: &[char], start: usize, end: usize) -> String {
    source[start..end].iter().collect()
}

fn masked_source(source: &str, source_file: &str, lines: &LineIndex) -> (Vec<char>, Vec<Value>) {
    let mut chars: Vec<char> = source.chars().collect();
    let mut diagnostics = Vec::new();
    let mut index = 0;

    while index < chars.len() {
        let end = if chars[index] == '-' && chars.get(index + 1) == Some(&'-') {
            Some(
                chars[index..]
                    .iter()
                    .position(|character| *character == '\n')
                    .map_or(chars.len(), |offset| index + offset),
            )
        } else if chars[index] == '/' && chars.get(index + 1) == Some(&'*') {
            let mut end = index + 2;
            while end + 1 < chars.len() && !(chars[end] == '*' && chars[end + 1] == '/') {
                end += 1;
            }
            if end + 1 >= chars.len() {
                diagnostics.push(json!({
                    "code": "UNTERMINATED_BLOCK_COMMENT",
                    "message": "Block comment is not closed; remaining text was ignored.",
                    "source_file": source_file,
                    "start_line": lines.line_at(index),
                    "end_line": lines.line_at(chars.len()),
                }));
                let source_length = chars.len();
                mask_span(&mut chars, index, source_length);
                break;
            }
            Some(end + 2)
        } else if chars[index] == '\'' {
            let mut end = index + 1;
            let mut terminated = false;
            while end < chars.len() {
                if chars[end] == '\'' && end + 1 < chars.len() && chars[end + 1] == '\'' {
                    end += 2;
                } else if chars[end] == '\'' {
                    end += 1;
                    terminated = true;
                    break;
                } else {
                    end += 1;
                }
            }
            if !terminated {
                diagnostics.push(json!({
                    "code": "UNTERMINATED_STRING",
                    "message": "String literal is not closed; remaining text was ignored.",
                    "source_file": source_file,
                    "start_line": lines.line_at(index),
                    "end_line": lines.line_at(chars.len()),
                }));
            }
            Some(end)
        } else if matches!(chars[index], 'q' | 'Q')
            && chars.get(index + 1) == Some(&'\'')
            && index + 2 < chars.len()
        {
            let closing = match chars[index + 2] {
                '[' => ']',
                '(' => ')',
                '{' => '}',
                '<' => '>',
                other => other,
            };
            let mut end = index + 3;
            while end + 1 < chars.len() && !(chars[end] == closing && chars[end + 1] == '\'') {
                end += 1;
            }
            if end + 1 >= chars.len() {
                diagnostics.push(json!({
                    "code": "UNTERMINATED_Q_STRING",
                    "message": "Alternative quoted string is not closed; remaining text was ignored.",
                    "source_file": source_file,
                    "start_line": lines.line_at(index),
                    "end_line": lines.line_at(chars.len()),
                }));
                Some(chars.len())
            } else {
                Some(end + 2)
            }
        } else {
            None
        };

        if let Some(end) = end {
            mask_span(&mut chars, index, end);
            index = end;
        } else {
            index += 1;
        }
    }

    (chars, diagnostics)
}

fn mask_span(chars: &mut [char], start: usize, end: usize) {
    for character in &mut chars[start..end] {
        if *character != '\n' {
            *character = ' ';
        }
    }
}

fn line_field(lines: &LineIndex, start: usize, end: usize) -> (usize, usize) {
    (lines.line_at(start), lines.line_at(end))
}

fn find_routine_end(masked: &str, header_end: usize) -> Option<usize> {
    let begin_regex = regex(r"(?i)\bBEGIN\b");
    let begin = begin_regex.find_at(masked, header_end)?;
    let begin_offset = begin.start();
    let tokens = regex(r"(?i)\b(?:BEGIN|IF|LOOP|CASE|END)\b");
    let following_pattern =
        regex(r"(?i)^\s*(?:(?P<kind>IF|LOOP|CASE)|(?P<name>[A-Za-z][A-Za-z0-9_$#]*))?\s*;");
    let mut stack = vec!["BEGIN".to_owned()];
    let mut skip_until = begin_offset;

    for token in tokens.find_iter(&masked[begin_offset..]) {
        let start = begin_offset + token.start();
        let end = begin_offset + token.end();
        if start < skip_until || start == begin_offset {
            continue;
        }
        let word = token.as_str().to_ascii_uppercase();
        if matches!(word.as_str(), "BEGIN" | "IF" | "LOOP" | "CASE") {
            stack.push(word);
            continue;
        }
        let captures = match following_pattern.captures(&masked[end..]) {
            Some(captures) => captures,
            None => continue,
        };
        if let Some(expected) = captures.name("kind") {
            if stack
                .last()
                .is_some_and(|item| item == &expected.as_str().to_ascii_uppercase())
            {
                stack.pop();
            }
        } else if stack.last().is_some_and(|item| item == "BEGIN") {
            stack.pop();
        }
        skip_until = end + captures.get(0).expect("whole match exists").end();
        if stack.is_empty() {
            return Some(skip_until);
        }
    }
    None
}

#[derive(Clone)]
struct Routine {
    kind: String,
    name: String,
    start: usize,
    header_end: usize,
    end: usize,
    parent: Option<usize>,
    object_id: String,
}

fn routine_pattern() -> Regex {
    regex(&format!(
        r"(?ims)^[ \t]*(?P<kind>PROCEDURE|FUNCTION)[ \t]+(?P<name>{IDENTIFIER})(?P<header>.*?)(?P<terminator>\bIS\b|\bAS\b|;)"
    ))
}

fn routine_candidates(
    masked: &[char],
    source_file: &str,
    lines: &LineIndex,
    diagnostics: &mut Vec<Value>,
) -> Vec<Routine> {
    let masked_string: String = masked.iter().collect();
    let map = CharMap::new(&masked_string);
    let pattern = routine_pattern();
    let mut routines = Vec::new();

    for captures in pattern.captures_iter(&masked_string) {
        let whole = captures.get(0).expect("whole regex match exists");
        let terminator = captures
            .name("terminator")
            .expect("terminator exists")
            .as_str();
        if terminator == ";" {
            continue;
        }
        let start = map.char_at(whole.start());
        let header_end = map.char_at(whole.end());
        let end = match find_routine_end(&masked_string, whole.end()) {
            Some(end) => map.char_at(end),
            None => {
                let kind = captures.name("kind").expect("kind exists").as_str();
                let title = if kind.eq_ignore_ascii_case("procedure") {
                    "Procedure"
                } else {
                    "Function"
                };
                let name = captures.name("name").expect("name exists").as_str();
                let (start_line, end_line) = line_field(lines, start, header_end);
                diagnostics.push(json!({
                    "code": "UNTERMINATED_ROUTINE",
                    "message": format!("{title} {name} has no named END delimiter."),
                    "source_file": source_file,
                    "start_line": start_line,
                    "end_line": end_line,
                }));
                header_end
            }
        };
        routines.push(Routine {
            kind: captures.name("kind").unwrap().as_str().to_ascii_uppercase(),
            name: captures.name("name").unwrap().as_str().to_ascii_uppercase(),
            start,
            header_end,
            end,
            parent: None,
            object_id: String::new(),
        });
    }

    routines.sort_by_key(|routine| (routine.start, std::cmp::Reverse(routine.end)));
    for index in 0..routines.len() {
        let current_start = routines[index].start;
        let current_end = routines[index].end;
        routines[index].parent = routines
            .iter()
            .enumerate()
            .filter(|(other, item)| {
                *other != index && item.start < current_start && current_end <= item.end
            })
            .min_by_key(|(_, item)| item.end - item.start)
            .map(|(parent, _)| parent);
    }
    routines
}

fn normalize(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn evidence(source: &[char], start: usize, end: usize) -> String {
    chars_slice(source, start, end).trim().to_owned()
}

fn matching_parenthesis(source: &[char], opening: usize) -> Option<usize> {
    let mut depth = 0;
    for (index, character) in source.iter().enumerate().skip(opening) {
        if *character == '(' {
            depth += 1;
        } else if *character == ')' {
            depth -= 1;
            if depth == 0 {
                return Some(index);
            }
        }
    }
    None
}

fn top_level_parts(value: &[char]) -> Vec<(usize, usize)> {
    let mut parts = Vec::new();
    let mut start = 0;
    let mut depth = 0;
    for (index, character) in value.iter().enumerate() {
        match character {
            '(' => depth += 1,
            ')' => depth -= 1,
            ',' if depth == 0 => {
                parts.push((start, index));
                start = index + 1;
            }
            _ => {}
        }
    }
    parts.push((start, value.len()));
    parts
}

fn optional_string(value: Option<String>) -> Value {
    value.map_or(Value::Null, Value::String)
}

fn entity(
    object_id: String,
    object_type: &str,
    parent_id: Option<String>,
    source_file: &str,
    start_line: usize,
    end_line: usize,
    signature: Option<String>,
    datatype: Option<String>,
    mode: Option<String>,
    evidence: Option<String>,
) -> Value {
    json!({
        "object_id": object_id,
        "object_type": object_type,
        "parent_id": parent_id,
        "source_file": source_file,
        "start_line": start_line,
        "end_line": end_line,
        "status": "resolved",
        "signature": optional_string(signature),
        "datatype": optional_string(datatype),
        "mode": optional_string(mode),
        "evidence": optional_string(evidence),
    })
}

fn parameters(
    source: &[char],
    masked: &[char],
    routine: &Routine,
    source_file: &str,
    lines: &LineIndex,
) -> Vec<Value> {
    let opening = match masked[routine.start..routine.header_end]
        .iter()
        .position(|character| *character == '(')
    {
        Some(offset) => routine.start + offset,
        None => return Vec::new(),
    };
    let closing = match matching_parenthesis(masked, opening) {
        Some(closing) if closing <= routine.header_end => closing,
        _ => return Vec::new(),
    };
    let parts = top_level_parts(&masked[opening + 1..closing]);
    let pattern = regex(&format!(
        r"(?is)^\s*(?P<name>{IDENTIFIER})\s+(?P<mode>IN\s+OUT|IN|OUT)?\s*(?:NOCOPY\s+)?(?P<datatype>.+?)\s*$"
    ));
    let mut entities = Vec::new();

    for (part_start, part_end) in parts {
        let fragment = chars_slice(masked, opening + 1 + part_start, opening + 1 + part_end);
        if let Some(captures) = pattern.captures(&fragment) {
            let start = opening + 1 + part_start;
            let end = opening + 1 + part_end;
            let (start_line, end_line) = line_field(lines, start, end);
            let name = captures.name("name").unwrap().as_str().to_ascii_uppercase();
            let datatype = normalize(captures.name("datatype").unwrap().as_str());
            let mode = normalize(captures.name("mode").map_or("IN", |value| value.as_str()))
                .to_ascii_uppercase();
            entities.push(entity(
                format!("{}.{}", routine.object_id, name),
                "PARAMETER",
                Some(routine.object_id.clone()),
                source_file,
                start_line,
                end_line,
                None,
                Some(datatype),
                Some(mode),
                Some(evidence(source, start, end)),
            ));
        }
    }
    entities
}

fn declarations(
    source: &[char],
    masked: &[char],
    routine: &Routine,
    source_file: &str,
    lines: &LineIndex,
) -> Vec<Value> {
    let body = chars_slice(masked, routine.header_end, routine.end);
    let begin = match regex(r"(?i)\bBEGIN\b").find(&body) {
        Some(begin) => routine.header_end + body[..begin.start()].chars().count(),
        None => return Vec::new(),
    };
    let end = begin;
    let cursor_pattern = regex(&format!(
        r"(?is)\bCURSOR\s+(?P<name>{IDENTIFIER})(?:\s*\([^;]*?\))?\s+IS\b"
    ));
    let type_pattern = regex(&format!(r"(?i)\bTYPE\s+(?P<name>{IDENTIFIER})\s+IS\b"));
    let variable_pattern = regex(&format!(
        r"(?is)^\s*(?P<names>{IDENTIFIER}(?:\s*,\s*{IDENTIFIER})*)\s+(?:CONSTANT\s+)?(?P<datatype>[^:=]+?)(?:\s*:=.*)?\s*$"
    ));
    let banned_pattern = regex(r"(?i)\b(?:PRAGMA|SUBTYPE)\b");
    let mut entities = Vec::new();
    let mut excluded_ranges: Vec<(usize, usize)> = Vec::new();

    let declaration_text = chars_slice(masked, routine.header_end, end);
    let declaration_map = CharMap::new(&declaration_text);
    let declaration_start = routine.header_end;

    for captures in cursor_pattern.captures_iter(&declaration_text) {
        let whole = captures.get(0).unwrap();
        let start = declaration_start + declaration_map.char_at(whole.start());
        let statement_end = masked[start + whole.as_str().chars().count()..end]
            .iter()
            .position(|character| *character == ';')
            .map_or(end, |offset| {
                start + whole.as_str().chars().count() + offset + 1
            });
        excluded_ranges.push((start, statement_end));
        let name = captures.name("name").unwrap().as_str().to_ascii_uppercase();
        let (start_line, end_line) = line_field(lines, start, statement_end);
        entities.push(entity(
            format!("{}.{}", routine.object_id, name),
            "CURSOR",
            Some(routine.object_id.clone()),
            source_file,
            start_line,
            end_line,
            None,
            None,
            None,
            Some(evidence(source, start, statement_end)),
        ));
    }

    for captures in type_pattern.captures_iter(&declaration_text) {
        let whole = captures.get(0).unwrap();
        let start = declaration_start + declaration_map.char_at(whole.start());
        let statement_end = masked[start + whole.as_str().chars().count()..end]
            .iter()
            .position(|character| *character == ';')
            .map_or(end, |offset| {
                start + whole.as_str().chars().count() + offset + 1
            });
        excluded_ranges.push((start, statement_end));
        let name = captures.name("name").unwrap().as_str().to_ascii_uppercase();
        let (start_line, end_line) = line_field(lines, start, statement_end);
        entities.push(entity(
            format!("{}.{}", routine.object_id, name),
            "TYPE",
            Some(routine.object_id.clone()),
            source_file,
            start_line,
            end_line,
            None,
            None,
            None,
            Some(evidence(source, start, statement_end)),
        ));
    }

    let mut statement_start = routine.header_end;
    for statement_end in routine.header_end..end {
        if masked[statement_end] != ';' {
            continue;
        }
        if excluded_ranges.iter().any(|(range_start, range_end)| {
            *range_start <= statement_start && statement_start < *range_end
        }) {
            statement_start = statement_end + 1;
            continue;
        }
        let statement = chars_slice(masked, statement_start, statement_end);
        if let Some(captures) = variable_pattern.captures(&statement) {
            if !banned_pattern.is_match(&statement) {
                let names = captures.name("names").unwrap().as_str();
                let datatype = normalize(captures.name("datatype").unwrap().as_str());
                for name in regex(r"\s*,\s*").split(names) {
                    let (start_line, end_line) =
                        line_field(lines, statement_start, statement_end + 1);
                    entities.push(entity(
                        format!("{}.{}", routine.object_id, name.to_ascii_uppercase()),
                        "VARIABLE",
                        Some(routine.object_id.clone()),
                        source_file,
                        start_line,
                        end_line,
                        None,
                        Some(datatype.clone()),
                        None,
                        Some(evidence(source, statement_start, statement_end + 1)),
                    ));
                }
            }
        }
        statement_start = statement_end + 1;
    }
    entities
}

fn scan_source(source: &str, source_file: &str) -> Value {
    let lines = LineIndex::new(source);
    let source_chars: Vec<char> = source.chars().collect();
    let (masked, mut diagnostics) = masked_source(source, source_file, &lines);
    let masked_string: String = masked.iter().collect();
    let map = CharMap::new(&masked_string);
    let package_pattern = regex(&format!(
        r"(?i)\bCREATE\s+OR\s+REPLACE\s+PACKAGE(?:\s+BODY)?\s+(?P<name>{IDENTIFIER})\b"
    ));
    let package_matches: Vec<_> = package_pattern.captures_iter(&masked_string).collect();
    let mut entities = Vec::new();
    let mut package_id = None;

    if package_matches.is_empty() {
        diagnostics.push(json!({
            "code": "PACKAGE_NOT_FOUND",
            "message": "No CREATE OR REPLACE PACKAGE declaration was found.",
            "source_file": source_file,
            "start_line": 1,
            "end_line": lines.line_at(source_chars.len()),
        }));
    } else {
        let first = &package_matches[0];
        let whole = first.get(0).unwrap();
        let start = map.char_at(whole.start());
        let end = map.char_at(whole.end());
        let name = first.name("name").unwrap().as_str().to_ascii_uppercase();
        package_id = Some(name.clone());
        entities.push(entity(
            name,
            "PACKAGE",
            None,
            source_file,
            lines.line_at(start),
            lines.line_at(source_chars.len()),
            None,
            None,
            None,
            Some(evidence(&source_chars, start, end)),
        ));
        if package_matches.len() > 1 {
            let second_start = map.char_at(package_matches[1].get(0).unwrap().start());
            let final_end = map.char_at(package_matches.last().unwrap().get(0).unwrap().end());
            let (start_line, end_line) = line_field(&lines, second_start, final_end);
            diagnostics.push(json!({
                "code": "MULTIPLE_PACKAGES",
                "message": "Only the first package is registered from a single source file.",
                "source_file": source_file,
                "start_line": start_line,
                "end_line": end_line,
            }));
        }
    }

    let mut routines = routine_candidates(&masked, source_file, &lines, &mut diagnostics);
    let mut name_counts = std::collections::BTreeMap::<(String, String), usize>::new();
    for index in 0..routines.len() {
        let parent = routines[index]
            .parent
            .map(|parent| routines[parent].object_id.clone())
            .or_else(|| package_id.clone())
            .unwrap_or_else(|| {
                Path::new(source_file)
                    .file_stem()
                    .and_then(|stem| stem.to_str())
                    .unwrap_or_default()
                    .to_ascii_uppercase()
            });
        let key = (parent.clone(), routines[index].name.clone());
        let count = name_counts.entry(key).or_insert(0);
        *count += 1;
        let suffix = if *count == 1 {
            String::new()
        } else {
            format!("#{}", *count)
        };
        routines[index].object_id = format!("{}.{}{}", parent, routines[index].name, suffix);
    }

    for routine in &routines {
        let parent_id = routine
            .parent
            .map(|parent| routines[parent].object_id.clone())
            .or_else(|| package_id.clone());
        let header = chars_slice(&masked, routine.start, routine.header_end);
        let return_pattern = regex(r"(?is)\bRETURN\s+(?P<datatype>.+?)(?:\bIS\b|\bAS\b)$");
        let datatype = return_pattern
            .captures(&header)
            .map(|captures| normalize(captures.name("datatype").unwrap().as_str()));
        let (start_line, end_line) = line_field(&lines, routine.start, routine.end);
        entities.push(entity(
            routine.object_id.clone(),
            &routine.kind,
            parent_id,
            source_file,
            start_line,
            end_line,
            Some(normalize(&header)),
            datatype,
            None,
            Some(evidence(&source_chars, routine.start, routine.header_end)),
        ));
        entities.extend(parameters(
            &source_chars,
            &masked,
            routine,
            source_file,
            &lines,
        ));
        entities.extend(declarations(
            &source_chars,
            &masked,
            routine,
            source_file,
            &lines,
        ));
    }

    entities.sort_by(|left, right| left["object_id"].as_str().cmp(&right["object_id"].as_str()));
    diagnostics.sort_by(|left, right| {
        (
            left["source_file"].as_str(),
            left["start_line"].as_u64(),
            left["code"].as_str(),
        )
            .cmp(&(
                right["source_file"].as_str(),
                right["start_line"].as_u64(),
                right["code"].as_str(),
            ))
    });
    json!({ "entities": entities, "diagnostics": diagnostics })
}

fn discover_sources(paths: &[PathBuf]) -> Result<Vec<PathBuf>, String> {
    fn visit(path: &Path, output: &mut Vec<PathBuf>) -> Result<(), String> {
        let entries = fs::read_dir(path).map_err(|error| format!("{}: {error}", path.display()))?;
        for entry in entries {
            let entry = entry.map_err(|error| format!("{}: {error}", path.display()))?;
            let child = entry.path();
            if child.is_dir() {
                let name = child
                    .file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or_default();
                if !IGNORED_DIRECTORIES.contains(&name) {
                    visit(&child, output)?;
                }
            } else if child.is_file()
                && child
                    .extension()
                    .and_then(|extension| extension.to_str())
                    .is_some_and(|extension| {
                        SOURCE_EXTENSIONS.iter().any(|suffix| {
                            suffix
                                .trim_start_matches('.')
                                .eq_ignore_ascii_case(extension)
                        })
                    })
            {
                output.push(child);
            }
        }
        Ok(())
    }

    let mut sources = Vec::new();
    for path in paths {
        if path.is_file() {
            if path
                .extension()
                .and_then(|extension| extension.to_str())
                .is_some_and(|extension| {
                    SOURCE_EXTENSIONS.iter().any(|suffix| {
                        suffix
                            .trim_start_matches('.')
                            .eq_ignore_ascii_case(extension)
                    })
                })
            {
                sources.push(path.clone());
            }
        } else if path.is_dir() {
            visit(path, &mut sources)?;
        }
    }
    for source in &mut sources {
        *source = source.components().collect();
    }
    let mut seen = HashSet::new();
    sources.retain(|path| seen.insert(path.clone()));
    sources.sort_by_key(|path| path.as_os_str().to_string_lossy().into_owned());
    Ok(sources)
}

pub fn scan_paths(paths: &[PathBuf]) -> Result<Value, String> {
    let sources = discover_sources(paths)?;
    let mut entities = Vec::new();
    let mut diagnostics = Vec::new();
    for path in sources {
        let source =
            fs::read_to_string(&path).map_err(|error| format!("{}: {error}", path.display()))?;
        let source_file = path.to_string_lossy().replace('\\', "/");
        let registry = scan_source(&source, &source_file);
        entities.extend(registry["entities"].as_array().unwrap().iter().cloned());
        diagnostics.extend(registry["diagnostics"].as_array().unwrap().iter().cloned());
    }
    entities.sort_by(|left, right| {
        (left["object_id"].as_str(), left["source_file"].as_str())
            .cmp(&(right["object_id"].as_str(), right["source_file"].as_str()))
    });
    diagnostics.sort_by(|left, right| {
        (
            left["source_file"].as_str(),
            left["start_line"].as_u64(),
            left["code"].as_str(),
        )
            .cmp(&(
                right["source_file"].as_str(),
                right["start_line"].as_u64(),
                right["code"].as_str(),
            ))
    });
    Ok(json!({ "entities": entities, "diagnostics": diagnostics }))
}

#[cfg(test)]
mod tests {
    use super::scan_source;

    #[test]
    fn registers_package_nested_routines_parameters_and_variables() {
        let source = "CREATE OR REPLACE PACKAGE BODY p AS\n\
            PROCEDURE outer_proc(p_id IN NUMBER) IS\n\
              v_total NUMBER := 1;\n\
              PROCEDURE inner_proc IS\n\
              BEGIN NULL; END inner_proc;\n\
            BEGIN NULL; END outer_proc;\n\
            END p;";
        let registry = scan_source(source, "sample.sql");
        let entities = registry["entities"].as_array().unwrap();

        assert_eq!(entities.len(), 5);
        assert_eq!(entities[0]["object_id"], "P");
        assert_eq!(entities[0]["object_type"], "PACKAGE");
        let inner = entities
            .iter()
            .find(|entity| entity["object_id"] == "P.OUTER_PROC.INNER_PROC")
            .unwrap();
        assert_eq!(inner["parent_id"], "P.OUTER_PROC");
        assert!(entities.iter().any(|entity| {
            entity["object_id"] == "P.OUTER_PROC.P_ID"
                && entity["object_type"] == "PARAMETER"
                && entity["datatype"] == "NUMBER"
        }));
        assert!(entities.iter().any(|entity| {
            entity["object_id"] == "P.OUTER_PROC.V_TOTAL" && entity["object_type"] == "VARIABLE"
        }));
        assert!(registry["diagnostics"].as_array().unwrap().is_empty());
    }

    #[test]
    fn masks_comments_and_literals_and_reports_unclosed_comment() {
        let source = "CREATE OR REPLACE PACKAGE BODY p AS\n\
            PROCEDURE actual IS\n\
              v_text VARCHAR2(20) := q'[PROCEDURE hidden IS END hidden;]';\n\
            BEGIN NULL; END actual;\n\
            /* unclosed";
        let registry = scan_source(source, "sample.sql");
        let entities = registry["entities"].as_array().unwrap();

        assert!(entities
            .iter()
            .any(|entity| entity["object_id"] == "P.ACTUAL"));
        assert!(!entities
            .iter()
            .any(|entity| entity["object_id"].as_str().unwrap().contains("HIDDEN")));
        assert_eq!(
            registry["diagnostics"][0]["code"],
            "UNTERMINATED_BLOCK_COMMENT"
        );
    }
}
