# Sample Reports

This directory contains example report definitions that demonstrate various InstantReports features.

## Available Reports

### 1. simple_sales_table.json
**Features demonstrated:**
- Basic table layout
- Parameterized queries ($year)
- Column formatting (currency, numbers)
- Sorting
- Page headers and footers
- Token substitution ({{date.now}}, {{page.number}})

**Use case:** Simple data export reports

---

### 2. regional_sales_summary.json
**Features demonstrated:**
- Grouping by field (region)
- Group headers and footers
- Subtotals
- Chart embedding (bar chart)
- Landscape page orientation
- Multiple sections (header, detail, summary, footer)

**Use case:** Managerial summary reports with drill-down capability

---

### 3. cross_tab_analysis.json
**Features demonstrated:**
- Cross-tab/pivot table generation
- Conditional formatting (cell-level)
- Color-coded thresholds
- Aggregation functions (sum, count)
- Sorted summary tables

**Use case:** Analytical reports requiring matrix-style data presentation

---

### 4. subreport_drilldown.json
**Features demonstrated:**
- Multiple data sources
- Subreport with parameter passing
- Drill-down render mode
- Parent-child relationships
- Pie and bar charts
- Excel export with subreports as separate sheets

**Use case:** Interactive reports where users need to explore detailed data

---

### 5. employee_performance.json
**Features demonstrated:**
- Calculated fields (sales_achievement_pct, revenue_per_call)
- IF/THEN expressions in calculated fields
- Conditional formatting (cell and row-level)
- Status indicators with color coding
- Department summary cross-tab
- Formatted percentages and currency

**Use case:** HR and management performance tracking reports

---

## Importing Sample Reports

To use these samples:

1. **Via Designer UI:**
   - Open the report designer
   - Click "Import Report" or "New from Template"
   - Select a JSON file from this directory

2. **Via API:**
   ```bash
   curl -X POST http://localhost:8000/designer/reports \
     -H "Authorization: Bearer <token>" \
     -F "name=Sample Report" \
     -F "definition=@simple_sales_table.json"
   ```

3. **Manual Import:**
   - Copy the JSON file
   - Paste into the report definition field in the designer

---

## Customizing Samples

Each sample report includes:
- `data_sources` - Configure your actual database connection
- `query` - Modify SQL to match your schema
- `parameters` - Add/remove as needed
- `layout.sections` - Adjust sections and elements
- `export_settings` - Enable/disable formats

**Note:** The queries assume a basic sales/employee schema. Adapt them to your actual database structure.

---

## Report Definition Schema

See the [InstantReports Documentation](../docs/) for the complete JSON schema reference.
