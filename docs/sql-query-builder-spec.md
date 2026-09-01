# SQL Query Builder - Feature Specification

## Overview
A visual query builder for InstantReports that allows users to construct SQL queries without writing SQL code manually. Integrates with table and chart elements in the report designer.

## Goals
- Enable non-technical users to build complex queries
- Reduce SQL errors through visual validation
- Speed up report creation workflow
- Provide query preview and testing before saving

## User Stories

### As a report designer, I want to:
1. Browse available tables and columns from my database connection
2. Visually select which columns to include in my query
3. Configure table joins with a visual interface
4. Add filters with field/operator/value selectors
5. Set grouping and sorting options
6. See the generated SQL in real-time
7. Test queries before saving
8. Save query configurations for reuse

## Technical Architecture

### Frontend Components

#### 1. Query Builder Modal
- **Location**: Opens from element properties (table/chart)
- **Layout**: Three-panel design
  - Left: Schema browser (tables/columns)
  - Center: Visual query canvas (steps)
  - Right: SQL preview + actions

#### 2. Schema Browser Panel
```
┌─────────────────────────┐
│ 📊 Tables               │
├─────────────────────────┤
│ 📋 employees            │
│   ├ employee_id (INT)   │
│   ├ first_name (VARCHAR)│
│   └ city (VARCHAR)      │
│                         │
│ 📋 orders               │
│   ├ order_id (INT)      │
│   └ order_date (DATE)   │
└─────────────────────────┘
```

#### 3. Query Steps (Visual Builder)
Each step is a card that can be added/removed/reordered:

**SELECT Step**
- Shows selected columns as tags
- Each tag displays: column name, aggregation (if any), table prefix
- Click to edit aggregation type (None, COUNT, SUM, AVG, MIN, MAX)

**FROM Step**
- Shows selected tables
- Primary table highlighted
- Drag to reorder (affects JOIN precedence)

**JOIN Step**
- Table selector dropdown
- Join type: INNER, LEFT, RIGHT, FULL
- Condition builder: field1 [operator] field2
- Visual line connecting joined tables

**WHERE Step**
- Filter rows with:
  - Field selector (with table prefix)
  - Operator selector (=, <, >, LIKE, IN, BETWEEN, IS NULL)
  - Value input (text, number, date, dropdown for IN)
- Multiple filters with AND/OR logic
- Group filters with parentheses

**GROUP BY Step**
- Selected columns as tags
- HAVING clause support (optional)

**ORDER BY Step**
- Field selector
- Direction: ASC/DESC
- Multiple sort fields with priority

### Backend API Endpoints

#### 1. GET /api/schema/{connection_id}
Returns table metadata for a connection.

**Response:**
```json
{
  "tables": [
    {
      "name": "employees",
      "columns": [
        {"name": "employee_id", "type": "INTEGER", "nullable": false},
        {"name": "first_name", "type": "VARCHAR(50)", "nullable": true}
      ]
    }
  ]
}
```

#### 2. POST /api/query/validate
Validates a query configuration and returns potential issues.

**Request:**
```json
{
  "query_config": { ... },
  "connection_id": "uuid"
}
```

**Response:**
```json
{
  "valid": true,
  "warnings": [],
  "suggestions": []
}
```

#### 3. POST /api/query/test
Execute a test query and return results preview.

**Request:**
```json
{
  "query_config": { ... },
  "connection_id": "uuid",
  "limit": 100
}
```

**Response:**
```json
{
  "success": true,
  "row_count": 42,
  "preview": [...],
  "execution_time_ms": 150
}
```

#### 4. POST /api/query/save
Save query configuration to database.

**Request:**
```json
{
  "query_config": { ... },
  "name": "Employee Sales Query",
  "description": "Top employees by total sales"
}
```

**Response:**
```json
{
  "id": "uuid",
  "name": "Employee Sales Query",
  "created_at": "2026-08-31T12:00:00Z"
}
```

## Data Structures

### Query Configuration (JSON)
```json
{
  "version": "1.0",
  "select": [
    {
      "table": "employees",
      "column": "first_name",
      "alias": null,
      "aggregation": null
    },
    {
      "table": "orders",
      "column": "order_id",
      "alias": "Order ID",
      "aggregation": "COUNT"
    }
  ],
  "from": ["employees"],
  "joins": [
    {
      "type": "LEFT",
      "table": "orders",
      "on": {
        "left_table": "employees",
        "left_column": "employee_id",
        "right_table": "orders",
        "right_column": "employee_id"
      }
    }
  ],
  "where": [
    {
      "field": "employees.city",
      "operator": "=",
      "value": "London",
      "logic": "AND"
    }
  ],
  "groupBy": ["employees.title"],
  "orderBy": [
    {
      "field": "orders.order_date",
      "direction": "DESC"
    }
  ]
}
```

### SQL Generation Rules

1. **SELECT**: Always explicit column list (no `*`)
2. **FROM**: Primary table first
3. **JOINs**: In order specified, with proper ON conditions
4. **WHERE**: Filters combined with AND/OR based on logic field
5. **GROUP BY**: Only non-aggregated select columns
6. **ORDER BY**: Applied after GROUP BY

## UI/UX Design Principles

1. **Progressive Disclosure**: Show basic options first, advanced options on demand
2. **Visual Feedback**: Highlight selected items, show connections between tables
3. **Error Prevention**: Validate at each step, prevent invalid configurations
4. **Real-time Preview**: SQL updates as user makes changes
5. **Undo/Redo**: Support query modification history
6. **Keyboard Shortcuts**: Common actions accessible via keyboard

## Implementation Phases

### Phase 1: Core Builder (MVP)
- [ ] Schema browser with table/column listing
- [ ] SELECT step with column selection
- [ ] FROM step with primary table
- [ ] Basic SQL generation
- [ ] Save/load query configurations

### Phase 2: Advanced Features
- [ ] JOIN configuration with visual editor
- [ ] WHERE filters with multiple conditions
- [ ] GROUP BY and ORDER BY steps
- [ ] Query validation API
- [ ] Test query execution

### Phase 3: Polish & Integration
- [ ] Drag-and-drop from schema to canvas
- [ ] Query history and versioning
- [ ] Saved query templates
- [ ] AI-assisted query suggestions
- [ ] Performance optimization indicators

## File Structure

```
app/
├── routes/
│   └── api/
│       ├── schema.py          # Schema browser endpoints
│       ├── query_builder.py   # Query builder endpoints
│       └── query_test.py      # Test execution endpoints
├── services/
│   ├── query_builder/
│   │   ├── config.py          # Query configuration models
│   │   ├── generator.py       # SQL generation logic
│   │   ├── validator.py       # Query validation
│   │   └── executor.py        # Query execution
│   └── connectors/
│       └── base.py            # Database connector interface
templates/
└── designer/
    └── query_builder.html     # Query builder modal template
static/
└── js/
    └── query_builder.js       # Frontend query builder logic
```

## Testing Requirements

### Unit Tests
- [ ] SQL generation from various configurations
- [ ] Query validation logic
- [ ] Schema parsing for different database types

### Integration Tests
- [ ] End-to-end query building workflow
- [ ] Database connection and schema retrieval
- [ ] Query execution and result handling

### UI Tests
- [ ] Modal open/close behavior
- [ ] Step addition/removal
- [ ] Real-time SQL preview updates
- [ ] Error handling and validation messages

## Success Metrics

1. **Adoption**: 50% of new reports use query builder within 3 months
2. **Efficiency**: Reduce query creation time by 60%
3. **Error Reduction**: 80% fewer SQL syntax errors in reports
4. **User Satisfaction**: 4.5/5 rating in user surveys

## Open Questions

1. Should we support multiple database types (PostgreSQL, MySQL, SQLite)?
2. How do we handle complex subqueries?
3. Should we add a "raw SQL" mode for advanced users?
4. How do we version query configurations?
5. What's the maximum complexity we should support visually?

## References

- [InstantReports Designer Documentation](../docs/designer.md)
- [Northwind Database Schema](https://github.com/pthom/northwind_psql)
- [SQL Builder UX Patterns](https://www.smashingmagazine.com/2021/06/building-sql-query-builder-ui/)

---

**Status**: Draft  
**Last Updated**: 2026-08-31  
**Owner**: InstantReports Team
