#[cfg(feature = "python-extension")]
use pyo3::exceptions::PyValueError;
#[cfg(feature = "python-extension")]
use pyo3::prelude::*;
#[cfg(feature = "python-extension")]
use regex::Regex;
#[cfg(feature = "python-extension")]
use std::cmp::Reverse;
#[cfg(feature = "python-extension")]
use std::sync::OnceLock;

pub mod registry;

#[cfg(feature = "python-extension")]
#[pyclass]
struct LineIndex {
    newline_positions: Vec<usize>,
    character_count: usize,
}

#[cfg(feature = "python-extension")]
#[pymethods]
impl LineIndex {
    #[new]
    fn new(source: &str) -> Self {
        let mut newline_positions = Vec::new();
        let mut character_count = 0;
        for (index, character) in source.chars().enumerate() {
            if character == '\n' {
                newline_positions.push(index);
            }
            character_count = index + 1;
        }
        Self {
            newline_positions,
            character_count,
        }
    }

    fn line_at(&self, offset: usize) -> PyResult<usize> {
        if offset > self.character_count {
            return Err(PyValueError::new_err(
                "source offset is outside the indexed text",
            ));
        }
        Ok(self
            .newline_positions
            .partition_point(|newline| *newline < offset)
            + 1)
    }
}

#[cfg(feature = "python-extension")]
fn mask_span(characters: &mut [char], start: usize, end: usize) {
    for character in &mut characters[start..end] {
        if *character != '\n' {
            *character = ' ';
        }
    }
}

#[cfg(feature = "python-extension")]
#[pyfunction]
fn mask_non_code(source: &str) -> (String, Vec<(String, String, usize, usize)>) {
    let mut characters: Vec<char> = source.chars().collect();
    let mut diagnostics = Vec::new();
    let mut index = 0;

    while index < characters.len() {
        if characters[index] == '-' && characters.get(index + 1) == Some(&'-') {
            let end = characters[index..]
                .iter()
                .position(|character| *character == '\n')
                .map_or(characters.len(), |offset| index + offset);
            mask_span(&mut characters, index, end);
            index = end;
        } else if characters[index] == '/' && characters.get(index + 1) == Some(&'*') {
            let mut end = index + 2;
            while end + 1 < characters.len()
                && !(characters[end] == '*' && characters[end + 1] == '/')
            {
                end += 1;
            }
            if end + 1 >= characters.len() {
                diagnostics.push((
                    "UNTERMINATED_BLOCK_COMMENT".to_owned(),
                    "Block comment is not closed; remaining text was ignored.".to_owned(),
                    index,
                    characters.len(),
                ));
                let source_length = characters.len();
                mask_span(&mut characters, index, source_length);
                break;
            }
            end += 2;
            mask_span(&mut characters, index, end);
            index = end;
        } else if characters[index] == '\'' {
            let mut end = index + 1;
            let mut terminated = false;
            while end < characters.len() {
                if characters[end] == '\''
                    && end + 1 < characters.len()
                    && characters[end + 1] == '\''
                {
                    end += 2;
                } else if characters[end] == '\'' {
                    end += 1;
                    terminated = true;
                    break;
                } else {
                    end += 1;
                }
            }
            if !terminated {
                diagnostics.push((
                    "UNTERMINATED_STRING".to_owned(),
                    "String literal is not closed; remaining text was ignored.".to_owned(),
                    index,
                    characters.len(),
                ));
            }
            mask_span(&mut characters, index, end);
            index = end;
        } else if matches!(characters[index], 'q' | 'Q')
            && characters.get(index + 1) == Some(&'\'')
            && index + 2 < characters.len()
        {
            let opening = characters[index + 2];
            let closing = match opening {
                '[' => ']',
                '(' => ')',
                '{' => '}',
                '<' => '>',
                other => other,
            };
            let mut end = index + 3;
            while end + 1 < characters.len()
                && !(characters[end] == closing && characters[end + 1] == '\'')
            {
                end += 1;
            }
            if end + 1 >= characters.len() {
                diagnostics.push((
                    "UNTERMINATED_Q_STRING".to_owned(),
                    "Alternative quoted string is not closed; remaining text was ignored."
                        .to_owned(),
                    index,
                    characters.len(),
                ));
                end = characters.len();
            } else {
                end += 2;
            }
            mask_span(&mut characters, index, end);
            index = end;
        } else {
            index += 1;
        }
    }

    (characters.into_iter().collect(), diagnostics)
}

#[cfg(feature = "python-extension")]
struct Candidate {
    kind: String,
    name: String,
    start: usize,
    header_end: usize,
    end: usize,
    parent: Option<usize>,
}

#[cfg(feature = "python-extension")]
fn routine_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| {
        Regex::new(
            r"(?ims)^[ \t]*(?P<kind>PROCEDURE|FUNCTION)[ \t]+(?P<name>[A-Za-z][A-Za-z0-9_$#]*)(?P<header>.*?)(?P<terminator>\bIS\b|\bAS\b|;)",
        )
        .expect("routine pattern is valid")
    })
}

#[cfg(feature = "python-extension")]
fn begin_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| Regex::new(r"(?i)\bBEGIN\b").expect("BEGIN pattern is valid"))
}

#[cfg(feature = "python-extension")]
fn token_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| {
        Regex::new(r"(?i)\b(?:BEGIN|IF|LOOP|CASE|END)\b").expect("token pattern is valid")
    })
}

#[cfg(feature = "python-extension")]
fn following_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| {
        Regex::new(r"(?i)^\s*(?:(?P<kind>IF|LOOP|CASE)|(?P<name>[A-Za-z][A-Za-z0-9_$#]*))?\s*;")
            .expect("END delimiter pattern is valid")
    })
}

#[cfg(feature = "python-extension")]
fn find_routine_end(masked: &str, header_end: usize) -> Option<usize> {
    let begin = begin_pattern().find_at(masked, header_end)?;
    let begin_offset = begin.start();
    let mut stack = vec!["BEGIN".to_owned()];
    let mut skip_until = begin_offset;

    for token in token_pattern().find_iter(&masked[begin_offset..]) {
        let token_start = begin_offset + token.start();
        let token_end = begin_offset + token.end();
        if token_start < skip_until {
            continue;
        }
        let word = token.as_str().to_ascii_uppercase();
        if token_start == begin_offset {
            continue;
        }
        if matches!(word.as_str(), "BEGIN" | "IF" | "LOOP" | "CASE") {
            stack.push(word);
            continue;
        }

        let following = match following_pattern().captures(&masked[token_end..]) {
            Some(captures) => captures,
            None => continue,
        };
        if let Some(expected) = following.name("kind") {
            if stack
                .last()
                .is_some_and(|item| item == &expected.as_str().to_uppercase())
            {
                stack.pop();
            }
        } else if stack.last().is_some_and(|item| item == "BEGIN") {
            stack.pop();
        }
        skip_until = token_end + following.get(0).expect("whole match exists").end();
        if stack.is_empty() {
            return Some(skip_until);
        }
    }
    None
}

#[cfg(feature = "python-extension")]
fn character_boundaries(source: &str) -> Vec<usize> {
    let mut boundaries: Vec<usize> = source.char_indices().map(|(offset, _)| offset).collect();
    boundaries.push(source.len());
    boundaries
}

#[cfg(feature = "python-extension")]
fn character_offset(boundaries: &[usize], byte_offset: usize) -> usize {
    boundaries
        .binary_search(&byte_offset)
        .expect("regex offsets must be UTF-8 character boundaries")
}

#[cfg(feature = "python-extension")]
#[pyfunction]
fn routine_candidates(
    masked: &str,
) -> (
    Vec<(String, String, usize, usize, usize, Option<usize>)>,
    Vec<(String, String, usize, usize)>,
) {
    let boundaries = character_boundaries(masked);
    let mut candidates = Vec::new();
    let mut diagnostics = Vec::new();

    for captures in routine_pattern().captures_iter(masked) {
        let whole = captures.get(0).expect("whole match exists");
        let terminator = captures
            .name("terminator")
            .expect("terminator capture exists")
            .as_str();
        if terminator == ";" {
            continue;
        }
        let start_byte = whole.start();
        let header_end_byte = whole.end();
        let start = character_offset(&boundaries, start_byte);
        let header_end = character_offset(&boundaries, header_end_byte);
        let end = match find_routine_end(masked, header_end_byte) {
            Some(end_byte) => character_offset(&boundaries, end_byte),
            None => {
                let kind = captures.name("kind").expect("kind capture exists").as_str();
                let title = if kind.eq_ignore_ascii_case("procedure") {
                    "Procedure"
                } else {
                    "Function"
                };
                let name = captures.name("name").expect("name capture exists").as_str();
                diagnostics.push((
                    "UNTERMINATED_ROUTINE".to_owned(),
                    format!("{title} {name} has no named END delimiter."),
                    start,
                    header_end,
                ));
                header_end
            }
        };
        candidates.push(Candidate {
            kind: captures
                .name("kind")
                .expect("kind capture exists")
                .as_str()
                .to_ascii_uppercase(),
            name: captures
                .name("name")
                .expect("name capture exists")
                .as_str()
                .to_ascii_uppercase(),
            start,
            header_end,
            end,
            parent: None,
        });
    }

    for index in 0..candidates.len() {
        let current_start = candidates[index].start;
        let current_end = candidates[index].end;
        let parent = candidates
            .iter()
            .enumerate()
            .filter(|(other_index, candidate)| {
                *other_index != index
                    && candidate.start < current_start
                    && current_end <= candidate.end
            })
            .min_by_key(|(_, candidate)| candidate.end - candidate.start)
            .map(|(parent_index, _)| parent_index);
        candidates[index].parent = parent;
    }

    let mut order: Vec<usize> = (0..candidates.len()).collect();
    order.sort_by_key(|index| (candidates[*index].start, Reverse(candidates[*index].end)));
    let mut new_indices = vec![0; candidates.len()];
    for (new_index, old_index) in order.iter().enumerate() {
        new_indices[*old_index] = new_index;
    }
    let routines = order
        .iter()
        .map(|index| {
            let candidate = &candidates[*index];
            (
                candidate.kind.clone(),
                candidate.name.clone(),
                candidate.start,
                candidate.header_end,
                candidate.end,
                candidate.parent.map(|parent| new_indices[parent]),
            )
        })
        .collect();

    (routines, diagnostics)
}

#[cfg(feature = "python-extension")]
#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<LineIndex>()?;
    module.add_function(wrap_pyfunction!(mask_non_code, module)?)?;
    module.add_function(wrap_pyfunction!(routine_candidates, module)?)?;
    Ok(())
}
