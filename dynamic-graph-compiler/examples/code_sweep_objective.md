# Code Sweep Objective: Security Vulnerability Scanner

## Objective Text

"Perform a security sweep across the entire codebase to identify potential SQL injection vulnerabilities, hardcoded credentials, and unsafe deserialization patterns. Generate a prioritized findings report grouped by severity (critical/high/medium/low) with file paths, line numbers, and remediation suggestions. Exclude third-party libraries and test files from the scan."

## Expected Graph Shape

```
                      [planner]
                         |
          +--------------+--------------+
          |              |              |
    [searcher]     [retriever]      [router]
          |              |              |
    [scorer]       [worker]        [worker]
          |              |              |
    [critic]       [critic]        [critic]
          |              |              |
    [reviewer]     [reviewer]     [reviewer]
          |              |              |
    [integrator]                      |
          |              |              |
    [reviewer]                       |
          |              |              |
    [approver] (merge + final review)
```

### Node Breakdown
- **planner**: Decomposes scan into pattern definitions, file traversal, detection, and reporting
- **searcher**: Finds relevant source files using glob patterns (*.py, *.js, *.java, *.go)
- **retriever**: Reads file contents for pattern matching
- **router**: Routes files to appropriate detectors (SQL detector, credential detector, deserialization detector)
- **scorer**: Assigns severity scores based on context (is string in query builder? is credential in production config?)
- **worker**: Performs regex and AST-based pattern detection per file
- **critic**: Validates findings are true positives (not false positives from similar names)
- **reviewer**: Reviews per-category findings for accuracy and proper severity classification
- **integrator**: Merges all category findings into unified report
- **approver**: Final sign-off before report delivery

### Expected Node Count: 28-35
### Expected Depth: 7-9

## Difficulty: HIGH

- Requires AST parsing or sophisticated regex for accurate detection
- Must distinguish between actual vulnerabilities and false positives (e.g., test code, commented examples)
- SQL injection detection needs to understand database query construction context
- Credential detection must distinguish between example/test credentials vs production secrets
- Deserialization detection needs to identify pickle.load, yaml.load, json.loads with external data

## Delegation Signals

The planner should signal delegation readiness with these markers:

```
[DELEGATE: search] -> [searcher] for file discovery
[DELEGATE: scan] -> [worker] for pattern detection per file
[DELEGATE: validate] -> [critic] for finding verification
[DELEGATE: report] -> [integrator] for merge operations
```

Workers should report completion with findings metadata:
```
[COMPLETE: scan] files_scanned=X findings=Y confidence=Z
```

## Key Constraints

1. **Read-only Execution**: No file modifications allowed in sweep mode
2. **Exclusion Patterns**: Must skip node_modules/, vendor/, .git/, *test*.py, *test*.js
3. **Critical Findings**: Any hardcoded password or eval()/pickle.load in production code = critical severity
4. **Confidence Threshold**: Findings with confidence < 0.7 should be flagged as uncertain
5. **No Shell Commands**: Shell execution is forbidden during sweep (use grep/find tools instead)
6. **Parallelism**: Use max_parallelism=4 to scan multiple files concurrently
7. **Timeout Per File**: Individual file analysis should timeout after 30 seconds to prevent hangs
8. **Memory Limits**: Track memory usage, abort scan if working set exceeds 500MB

## Profile Recommendation

Use `code` profile for this objective. The profile allows read_file, grep, glob, and search tools which are sufficient. The `write_file` tool is forbidden to prevent accidental fix implementation during sweep.

## Side Effects to Approve

- `file_write`: Only approved for writing the final JSON/CSV report (reviewer must approve content before write)
- `network_call`: Should be disabled - this is a purely local code analysis task

## Output Format

The final report should contain:
- Executive summary with counts by severity
- Detailed findings array with: file, line, severity, pattern_matched, confidence, remediation
- Excluded files list with reason for exclusion
- Scan metadata: files_scanned, time_elapsed, patterns_used