# Research Objective: Texas Companies Analysis

## Objective Text

"Find all companies in Texas that operate in the renewable energy sector, specifically solar and wind installation services. For each company, gather: company name, website URL, physical address, primary contact name if available, years in business, and any public social proof (reviews, certifications, awards). Compile into a structured CSV with columns for each data field, plus a summary report with market analysis insights."

## Expected Graph Shape

```
                      [planner]
                         |
         +--------------+-------------+-------------+
         |              |             |             |
    [searcher]    [fetcher]       [scorer]      [router]
         |              |             |             |
    [worker]       [worker]       [worker]       [worker]
         |              |             |             |
    [critic]        [critic]       [critic]       [critic]
         |              |             |             |
    [reviewer]     [reviewer]     [reviewer]     [reviewer]
         |              |             |             |
         +--------------+-------------+-------------+
                          |
                    [integrator]
                          |
                     [reviewer]
                          |
                     [approver]
```

### Node Breakdown
- **planner**: Decomposes research into business registration search, web scraping, contact discovery, and data synthesis
- **searcher**: Searches Texas business registries, renewable energy industry directories, and local chamber of commerce listings
- **fetcher**: Retrieves company websites, extracts contact information, gathers social proof data
- **scorer**: Scores data quality and completeness (did we get address? contact? reviews?)
- **router**: Routes companies to correct data gathering paths (Sole prop vs Corp vs LLC handling differs)
- **worker**: Performs individual company research tasks
- **critic**: Validates gathered data against known patterns (real address vs PO Box, valid phone format)
- **reviewer**: Reviews per-region findings for consistency and completeness
- **integrator**: Merges all data into structured CSV rows and summary statistics
- **approver**: Final review of compiled dataset before delivery

### Expected Node Count: 24-30
### Expected Depth: 6-7

## Difficulty: MODERATE

- Texas has over 100,000 active businesses, must limit scope via filters
- Renewable energy sector definitions vary - need clear inclusion/exclusion criteria
- Data quality varies significantly - PO Box addresses, generic contact forms, missing social proof
- Web scraping must respect robots.txt and rate limits
- Some companies may have outdated or missing online presence

## Safe Mode Considerations

1. **Network Calls**: Enabled for research profile - web fetching and search are essential
2. **Rate Limiting**: Max 30 requests/minute to avoid being blocked by target websites
3. **No File Write**: Data should be returned via graph output, not written to disk
4. **Secret Files**: Customer lists and proprietary data should be flagged and excluded
5. **Data Validation**: All gathered data should be validated by critic before integration
6. **Privacy**: Do not collect personal identifiable information beyond business contact names
7. **Citation Requirements**: All data must include source URL for verification

## Key Constraints

1. **Geographic Filter**: Must use "Texas", "TX", "Austin", "Houston", "Dallas" etc. in search queries
2. **Sector Filter**: Use "solar installation", "wind installation", "renewable energy contractor", "clean energy"
3. **Data Freshness**: Only include companies with websites updated in last 3 years or SEC filings in last 12 months
4. **Exclusion**: Exclude companies that primarily manufacture solar panels/wind turbines (installation only)
5. **Minimum Data**: Only include companies with at least company name, website, and phone/address
6. **User Agent**: Use descriptive user agent identifying this research agent for transparency
7. **Timeout Per Company**: Individual company research should timeout after 60 seconds
8. **Max Results**: Cap at 500 companies to prevent runaway graph expansion

## Profile Recommendation

Use `research` profile. This objective requires:
- `search_web` and `fetch_url` for gathering company data
- `read_file` and `glob` for processing any local source files (e.g., existing lead lists)
- No file write capability - results returned via graph output
- Network calls enabled with rate limiting

## Side Effects to Approve

- `network_call`: Required for all web searches and fetches
- `browser_action`: Some websites require JavaScript rendering to extract contact info
- `file_write`: Only for final CSV export (reviewer-approved)

## Output Format

### CSV Structure
| company_name | website | address | city | state | zip | contact_name | contact_title | phone | email | years_in_business | reviews_count | certifications | source_url | data_gathered_date |

### Summary Report
- Total companies found: X
- Companies with complete data: Y (%)
- Geographic distribution by Texas city
- Most common certifications found
- Companies with social proof (reviews/awards): Z