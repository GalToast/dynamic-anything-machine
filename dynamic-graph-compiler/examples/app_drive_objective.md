# Google Drive App Integration Objective

## Objective Text

"Build a dynamic agent graph that connects to a user's Google Drive account, scans all shared documents and folders, identifies documents that haven't been accessed in over 90 days, and generates a summary report of stale documents organized by owner with suggested archival actions."

## Expected Graph Shape

```
                    [planner]
                       |
         +-------------+-------------+
         |             |             |
    [retriever]   [router]      [integrator]
         |             |             |
    [scorer]      [searcher]    [reviewer]
         |             |
    [worker]      [worker]
         |             |
    [critic]     [critic]
         |             |
    [reviewer]  [reviewer]
         |
    [approver] (if high_risk mode)
```

### Node Breakdown
- **planner**: Decomposes into drive auth, document scan, stale detection, and report generation
- **retriever**: Authenticates with Google Drive API, fetches file metadata
- **router**: Routes files to correct handlers based on mime type and access patterns
- **scorer**: Scores documents by last access time, creates staleness metrics
- **searcher**: Queries Drive for permission changes and sharing metadata
- **worker**: Processes individual document staleness calculations
- **critic**: Validates staleness thresholds and archival recommendations
- **reviewer**: Ensures report formatting meets requirements and no PII exposure
- **integrator**: Aggregates per-owner summaries into final report

### Expected Node Count: 18-22
### Expected Depth: 6-8

## Difficulty: MODERATE to HIGH

- Requires OAuth2 credential handling with proper secret protection
- Must handle paginated Drive API responses
- Needs rate limiting to avoid API quota exhaustion
- Involves date arithmetic and timezone handling
- Report generation requires structured output with grouping logic

## Key Constraints

1. **Authentication**: Must use secure credential flow, cannot store tokens in plain text
2. **Secret Protection**: OAuth tokens and client secrets must be treated as secret_files
3. **Network Calls**: Drive API calls require network access approval
4. **No Arbitrary Write**: Report output should be written via reviewer-approved mechanism
5. **Critic Gate**: Staleness threshold of 90 days must be validated before scoring
6. **Reviewer Gate**: Final report must pass review before presentation
7. **Delegation Depth**: Max 2 levels (planner -> workers, workers cannot further delegate)
8. **Rate Limiting**: Drive API has 1000 req/min quota, must implement request batching

## Profile Recommendation

Use `research` profile initially for discovery phase, then escalate to `code` profile if archival action implementation is needed. The `high_risk` profile is appropriate if the report will be auto-emailed or posted to shared channels.

## Side Effects to Approve

- `network_call`: Google Drive API authentication and file metadata fetch
- `file_write`: Saving the generated report (requires reviewer approval)
- `expose_secret`: Handling OAuth tokens in memory during graph execution