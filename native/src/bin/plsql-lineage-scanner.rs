use std::env;
use std::fs;
use std::path::PathBuf;
use std::process::ExitCode;

fn run() -> Result<(), String> {
    let mut paths = Vec::new();
    let mut output = None;
    let mut arguments = env::args().skip(1);

    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "-o" | "--output" => {
                let value = arguments
                    .next()
                    .ok_or_else(|| format!("{argument} requires an output path"))?;
                output = Some(PathBuf::from(value));
            }
            "--version" => {
                println!("plsql-lineage-scanner {}", env!("CARGO_PKG_VERSION"));
                return Ok(());
            }
            "-h" | "--help" => {
                println!(
                    "Build a Phase 1 PL/SQL object registry.\n\nUsage: plsql-lineage-scanner <source-file-or-directory>... -o <registry.json>"
                );
                return Ok(());
            }
            _ if argument.starts_with('-') => {
                return Err(format!("unknown option: {argument}"));
            }
            _ => paths.push(PathBuf::from(argument)),
        }
    }

    if paths.is_empty() {
        return Err("at least one source path is required".to_owned());
    }
    let output =
        output.ok_or_else(|| "required output option -o/--output is missing".to_owned())?;
    let registry = _native::registry::scan_paths(&paths)?;
    let serialized = serde_json::to_string_pretty(&registry)
        .map_err(|error| format!("could not serialize registry: {error}"))?;
    fs::write(&output, format!("{serialized}\n"))
        .map_err(|error| format!("{}: {error}", output.display()))?;
    Ok(())
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("plsql-lineage-scanner: {error}");
            ExitCode::FAILURE
        }
    }
}
