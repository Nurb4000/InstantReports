# Demo Reports Documentation

## Overview

The `demos/` directory contains sample report definitions that demonstrate InstantReports capabilities. These reports can be imported via the "Import Report" button in the designer or used as reference templates.

---

## Database Requirements

### Northwind Demo Database

**Connection Details:**
- **Host:** `10.0.1.33`
- **Port:** `5432`
- **Database:** `northwind`
- **User:** `northwind`
- **Password:** `northwind`
- **Schema:** `public`

**Connection ID:** `bef2f9f6-3cb6-47db-941c-fec47999a3c8`

**Required Tables:**
The following tables must exist in the Northwind database for the demos to work:

| Table | Description |
|-------|-------------|
| `employees` | Employee information (employee_id, first_name, last_name, title, city, hire_date) |
| `orders` | Order information (order_id, employee_id, customer_id, order_date, shipped_date) |
| `order_details` | Order line items (order_id, product_id, unit_price, quantity) |
| `products` | Product information (product_id, product_name, category_id, unit_price, units_in_stock) |
| `categories` | Product categories (category_id, category_name) |
| `customers` | Customer information (customer_id, company_name, contact_name, city, country) |

**Schema Notes:**
- All table and column names use **snake_case** (e.g., `employee_id`, not `employeeid`)
- String columns use `VARCHAR(n)` with appropriate lengths
- Numeric columns use `INTEGER` or `DECIMAL(p,s)` as appropriate
- Date columns use `DATE` type
- Foreign keys follow the pattern: `{table}_id` referencing the primary key in the related table

---

## Demo Reports

### 1. All Elements Demo (`all_elements_demo.ir.json`)

**Purpose:** Showcase all element types and section types available in InstantReports.

**Sections:**
- **Header:** Company Report Header (custom name)
  - Text element: Welcome message
  - Image element: Company logo (requires `/static/img/logo.png`)
- **Detail:** Employee Sales Overview (custom name)
  - Text element: Description
  - Table element: Employee sales data with live query
  - Chart element: Visual representation of employee sales
- **Detail:** Product Analysis (custom name)
  - Text element: Description
  - Table element: Product category sales data
  - Chart element: Category sales visualization
- **Summary:** Report Summary (custom name)
  - Text element: Report description
- **Footer:** Report Footer (custom name)
  - Text element: Confidential footer

**Data Source:** Northwind Demo (live database)

**Key Features Demonstrated:**
- All section types (header, detail, summary, footer)
- All element types (text, image, table, chart)
- Custom section names
- Custom element labels
- Live SQL queries against Northwind database
- Chart data visualization

**SQL Queries Used:**

```sql
-- Employee Sales Table
SELECT e.employee_id, 
       e.first_name || ' ' || e.last_name AS employee, 
       COUNT(o.order_id) AS orders, 
       SUM(od.unit_price * od.quantity) AS total_sales 
FROM employees e 
LEFT JOIN orders o ON e.employee_id = o.employee_id 
LEFT JOIN order_details od ON o.order_id = od.order_id 
GROUP BY e.employee_id, e.first_name, e.last_name 
ORDER BY total_sales DESC 
LIMIT 10

-- Employee Sales Chart
SELECT e.first_name || ' ' || e.last_name AS employee, 
       SUM(od.unit_price * od.quantity) AS total_sales 
FROM employees e 
LEFT JOIN orders o ON e.employee_id = o.employee_id 
LEFT JOIN order_details od ON o.order_id = od.order_id 
GROUP BY e.employee_id, e.first_name, e.last_name 
ORDER BY total_sales DESC 
LIMIT 10

-- Product Sales Table
SELECT c.category_name, 
       COUNT(DISTINCT p.product_id) AS products, 
       SUM(od.quantity) AS total_units, 
       SUM(od.unit_price * od.quantity) AS total_sales 
FROM categories c 
LEFT JOIN products p ON c.category_id = p.category_id 
LEFT JOIN order_details od ON p.product_id = od.product_id 
GROUP BY c.category_name 
ORDER BY total_sales DESC

-- Category Sales Chart
SELECT c.category_name, 
       SUM(od.unit_price * od.quantity) AS total_sales 
FROM categories c 
LEFT JOIN products p ON c.category_id = p.category_id 
LEFT JOIN order_details od ON p.product_id = od.product_id 
GROUP BY c.category_name 
ORDER BY total_sales DESC
```

---

### 2. Employee Sales Performance (`employee_sales.ir.json`)

**Purpose:** Demonstrate employee sales analysis with order counts and total sales.

**Sections:**
- **Header:** Title text
- **Detail:** Table showing employee sales performance
- **Summary:** Report period information
- **Footer:** Confidentiality notice

**Data Source:** Northwind Demo (live database)

**SQL Query:**
```sql
SELECT e.employee_id, 
       e.first_name || ' ' || e.last_name as employee_name, 
       COUNT(o.order_id) as total_orders, 
       SUM(od.unit_price * od.quantity) as total_sales 
FROM employees e 
LEFT JOIN orders o ON e.employee_id = o.employee_id 
LEFT JOIN order_details od ON o.order_id = od.order_id 
GROUP BY e.employee_id, e.first_name, e.last_name 
ORDER BY total_sales DESC
```

**Key Features:**
- Multi-table JOIN (employees → orders → order_details)
- Aggregate functions (COUNT, SUM)
- String concatenation (first_name || ' ' || last_name)
- ORDER BY with DESC
- Custom column aliases

---

### 3. Product Inventory Report (`product_inventory.ir.json`)

**Purpose:** Show product inventory levels by category.

**Sections:**
- **Header:** Title text
- **Detail:** Table with product inventory data
- **Summary:** Total product count (uses template variable `{{count}}`)
- **Footer:** Confidentiality notice

**Data Source:** Northwind Demo (live database)

**SQL Query:**
```sql
SELECT p.product_id, 
       p.product_name, 
       c.category_name, 
       p.unit_price, 
       p.units_in_stock, 
       p.reorder_level 
FROM products p 
JOIN categories c ON p.category_id = c.category_id 
ORDER BY c.category_name, p.product_name
```

**Key Features:**
- JOIN between products and categories
- Multiple column selection
- ORDER BY with multiple fields
- Template variable usage in summary section

---

### 4. Sales Summary Report (`sales_summary.ir.json`)

**Purpose:** Display recent orders with customer information.

**Sections:**
- **Header:** Title text
- **Detail:** Table with order and customer data
- **Footer:** Generation timestamp (uses template variable `{{date.now}}`)

**Data Source:** Northwind Demo (live database)

**SQL Query:**
```sql
SELECT o.order_id, 
       o.order_date, 
       c.company_name, 
       c.contact_name, 
       o.shipped_date 
FROM orders o 
JOIN customers c ON o.customer_id = c.customer_id 
ORDER BY o.order_date DESC 
LIMIT 50
```

**Key Features:**
- JOIN between orders and customers
- Date ordering (most recent first)
- LIMIT clause for pagination
- Template variable for dynamic date

---

### 5. Simple Customer List (`simple_customers.ir.json`)

**Purpose:** Basic customer listing without live database dependency.

**Sections:**
- **Header:** Title text
- **Detail:** Table with customer information
- **Footer:** Report attribution

**Data Source:** None (static/simulated data)

**SQL Query:**
```sql
SELECT customer_id, 
       company_name, 
       contact_name, 
       city, 
       country 
FROM customers 
ORDER BY company_name
```

**Key Features:**
- Simple single-table query
- No data source connection required
- Basic column selection
- ORDER BY alphabetically

**Note:** This report has an empty `data_sources` array, meaning it's intended for demonstration purposes only. In a production environment, you would need to configure a database connection and update the `connection_id` in the `data_sources` array.

---

## Importing Demos

### Via Designer UI
1. Navigate to **Report Designer**
2. Click **Import Report** button
3. Select the `.ir.json` file from the `demos/` directory
4. The report will be imported and available for editing

### Via API
```bash
curl -X POST http://localhost:8000/designer/reports/import \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@demos/employee_sales.ir.json"
```

---

## Customization Guide

### Updating Database Connection

If your Northwind database is hosted at a different location, update the `connection_id` in the demo files:

1. Create a new data connection in the designer
2. Note the connection ID from the URL or connection list
3. Update the `data_sources.connection_id` field in the demo JSON file

### Modifying SQL Queries

All SQL queries are stored in the `definition.layout.sections[].elements[].properties.query` field. You can:

1. Open the report in the designer
2. Edit any table element's properties
3. Modify the SQL query
4. Save the report

### Adding New Demos

To create a new demo report:

1. Copy an existing demo file as a template
2. Update the `name` and `description` fields
3. Modify the sections and elements as needed
4. Update the SQL queries to match your schema
5. Set the correct `connection_id` in `data_sources`
6. Save with a descriptive filename (e.g., `my_custom_report.ir.json`)

---

## Troubleshooting

### "Relation does not exist" Errors

If you see errors like `relation "employees" does not exist`, verify:

1. The Northwind database is running and accessible at `10.0.1.33:5432`
2. The `northwind` user has proper permissions
3. The schema is `public` (or update the connection config)
4. All required tables exist with the correct names

### Image Not Displaying

For reports using images (like `all_elements_demo.ir.json`):

1. Ensure the image file exists at `/static/img/logo.png`
2. Check file permissions (readable by the application)
3. Verify the path in the element properties matches the actual location

### Empty Results

If tables show no data:

1. Verify the Northwind database has sample data
2. Check that the SQL query is valid for your schema
3. Ensure the connection ID in `data_sources` is correct

---

## Future Enhancements

- [ ] Add more demo reports showcasing different features
- [ ] Include reports with subreports and crosstabs
- [ ] Add reports with conditional formatting
- [ ] Create demo reports for different database schemas
- [ ] Add export templates for common report types

---

**Last Updated:** 2026-08-31  
**Version:** 1.0  
**Status:** Production Ready
