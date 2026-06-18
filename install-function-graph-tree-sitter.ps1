param(
    [switch]$SkipInstall,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$requirementsPath = Join-Path $repoRoot "requirements-function-graph-optional.txt"
if (-not (Test-Path -LiteralPath $requirementsPath)) {
    throw "Missing optional requirements file: $requirementsPath"
}

if (-not $SkipInstall) {
    python -m pip install -r $requirementsPath
}

python -c "import sys; sys.path.insert(0, 'src/indexer'); from cpp_function_graph_tree_sitter import tree_sitter_cpp_dependency_status; print(tree_sitter_cpp_dependency_status())"

if (-not $SkipTests) {
    python -m unittest tests.test_cpp_function_graph_extract
}
