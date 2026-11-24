# Using this with Spirent Landslide

This framework does not control Landslide. It reads a results file you export and
checks it against pass criteria, so a Landslide run becomes a pass/fail line in the
same report as the functional tests.

I have not seen your Landslide version or export, so nothing here assumes specific
menus or column names. Use your team's existing test templates.

## Steps

1. **Run your interworking test in the Landslide GUI** with your team's usual test
   case (UE count, handover rate, duration). Write the parameters in the table below
   before you start so the result is reproducible.
2. **Export the results as CSV** (interval-level results, one row per reporting interval).
3. **Open the CSV and note the header names** for: attempts, successes, and an
   average time metric (handover interruption or setup time).
4. **Edit `landslide/landslide_columns.json`** so the three values match those headers
   exactly. If a name is wrong, the tool lists the headers it found.
5. **Edit `landslide/landslide_criteria.json`** to your team's acceptance limits. The
   defaults (99% success, 100 ms average, 150 ms P95) are placeholders, not a standard.
6. **Run:**
   ```bash
   python3 tools/landslide_report.py your_export.csv
   python3 run_suite.py --landslide-csv your_export.csv      # adds it to the HTML report
   ```

## Record each run

| Item | Value |
|---|---|
| Date / build under test | |
| Landslide version | |
| Test case / template name | |
| Subscribers (UEs) | |
| Handover or mobility rate | |
| Duration | |
| Pass criteria used | |
| Result (PASS/FAIL) and notes | |

## Notes on the numbers

- "P95 of interval averages" is the 95th percentile across reporting intervals of the
  per-interval average. It is not a per-event P95. Use the exact label when you quote it.
- Do not mix simulated results and Landslide results in one claim. The simulated
  KPIs prove the framework; the Landslide numbers describe the real system.
