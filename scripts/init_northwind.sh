#!/bin/bash
set -e

# This script runs after postgres initialization to seed the northwind database
echo "Seeding northwind database..."

# Create northwind user and database if they don't exist
psql -v ON_ERROR_STOP=0 --username "$POSTGRES_USER" --dbname "postgres" -c "CREATE USER northwind WITH PASSWORD 'northwind' SUPERUSER;" 2>/dev/null || echo "User may already exist"
psql -v ON_ERROR_STOP=0 --username "$POSTGRES_USER" --dbname "postgres" -c "CREATE DATABASE northwind OWNER northwind;" 2>/dev/null || echo "Database may already exist"

# Wait for northwind database to be available
echo "Waiting for northwind database to be ready..."
for i in $(seq 1 30); do
    if psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "northwind" -c "SELECT 1;" > /dev/null 2>&1; then
        echo "Database is ready!"
        break
    fi
    echo "Attempt $i/30: Database not ready, waiting..."
    sleep 1
done

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "northwind" <<-EOSQL
    -- Create tables
    CREATE TABLE IF NOT EXISTS customers (
        customer_id SERIAL PRIMARY KEY,
        company_name TEXT NOT NULL,
        contact_name TEXT NOT NULL,
        contact_title TEXT,
        address TEXT,
        city TEXT,
        region TEXT,
        postal_code TEXT,
        country TEXT,
        phone TEXT,
        fax TEXT
    );

    CREATE TABLE IF NOT EXISTS employees (
        employee_id SERIAL PRIMARY KEY,
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        title TEXT,
        title_of_courtesy TEXT,
        birth_date TIMESTAMP,
        hire_date TIMESTAMP,
        address TEXT,
        city TEXT,
        region TEXT,
        postal_code TEXT,
        country TEXT,
        home_phone TEXT,
        extension INTEGER,
        notes TEXT,
        reports_to INTEGER REFERENCES employees(employee_id),
        photo BYTEA
    );

    CREATE TABLE IF NOT EXISTS suppliers (
        supplier_id SERIAL PRIMARY KEY,
        company_name TEXT NOT NULL,
        contact_name TEXT,
        contact_title TEXT,
        address TEXT,
        city TEXT,
        region TEXT,
        postal_code TEXT,
        country TEXT,
        phone TEXT,
        fax TEXT,
        website TEXT
    );

    CREATE TABLE IF NOT EXISTS categories (
        category_id SERIAL PRIMARY KEY,
        category_name TEXT NOT NULL,
        description TEXT,
        picture BYTEA
    );

    CREATE TABLE IF NOT EXISTS products (
        product_id SERIAL PRIMARY KEY,
        product_name TEXT NOT NULL,
        supplier_id INTEGER REFERENCES suppliers(supplier_id),
        category_id INTEGER REFERENCES categories(category_id),
        unit TEXT,
        price NUMERIC(10,2) DEFAULT 0,
        discontinued BOOLEAN DEFAULT FALSE
    );

    CREATE TABLE IF NOT EXISTS shippers (
        shipper_id SERIAL PRIMARY KEY,
        company_name TEXT NOT NULL,
        phone TEXT
    );

    CREATE TABLE IF NOT EXISTS orders (
        order_id SERIAL PRIMARY KEY,
        customer_id INTEGER REFERENCES customers(customer_id),
        employee_id INTEGER REFERENCES employees(employee_id),
        order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        required_date TIMESTAMP,
        shipped_date TIMESTAMP,
        shipper_id INTEGER REFERENCES shippers(shipper_id),
        freight NUMERIC(10,2) DEFAULT 0,
        ship_name TEXT,
        ship_address TEXT,
        ship_city TEXT,
        ship_region TEXT,
        ship_postal_code TEXT,
        ship_country TEXT
    );

    CREATE TABLE IF NOT EXISTS order_details (
        order_detail_id SERIAL PRIMARY KEY,
        order_id INTEGER REFERENCES orders(order_id) ON DELETE CASCADE,
        product_id INTEGER REFERENCES products(product_id),
        unit_price NUMERIC(10,2) NOT NULL DEFAULT 0,
        quantity SMALLINT NOT NULL DEFAULT 1,
        discount NUMERIC(5,2) NOT NULL DEFAULT 0
    );

    -- Seed customers
    INSERT INTO customers (company_name, contact_name, contact_title, city, region, postal_code, country, phone) VALUES
    ('Alfreds Futterkiste', 'Maria Anders', 'Sales Representative', 'Berlin', NULL, '12209', 'Germany', '+49 30 0074321'),
    ('Ana Trujillo Emparedados y helados', 'Ana Trujillo', 'Owner', 'México D.F.', NULL, '05021', 'Mexico', '(5) 555-4729'),
    ('Antonio Moreno Taquería', 'Antonio Moreno', 'Owner', 'México D.F.', NULL, '05023', 'Mexico', '(5) 555-3932'),
    ('Around the Horn', 'Thomas Hardy', 'Sales Representative', 'London', NULL, 'WX1 6LT', 'UK', '(171) 555-7788'),
    ('Berglunds snabbköp', 'Christina Berglund', 'Export Manager', 'Luleå', NULL, 'S-958 12', 'Sweden', '0921-12 34 65')
    ON CONFLICT DO NOTHING;

    -- Seed employees
    INSERT INTO employees (first_name, last_name, title, city, country, hire_date) VALUES
    ('Nancy', 'Davolio', 'Sales Representative', 'Seattle', 'USA', '1992-05-01'),
    ('Andrew', 'Fuller', 'Vice President, Sales', 'Tacoma', 'USA', '1992-08-14'),
    ('Janet', 'Leverling', 'Sales Representative', 'Kirkland', 'USA', '1992-04-01')
    ON CONFLICT DO NOTHING;

    -- Seed categories
    INSERT INTO categories (category_name, description) VALUES
    ('Beverages', 'Soft drinks, coffees, teas, beers, and ales'),
    ('Condiments', 'Sweet and savory sauces, relishes, spreads, and seasonings'),
    ('Confections', 'Desserts, candies, and sweet breads')
    ON CONFLICT DO NOTHING;

    -- Seed suppliers
    INSERT INTO suppliers (company_name, contact_name, city, country) VALUES
    ('Exotic Liquids', 'Charlotte Cooper', 'London', 'UK'),
    ('New Orleans Cajun Delights', 'Shelley Burke', 'New Orleans', 'USA')
    ON CONFLICT DO NOTHING;

    -- Seed shippers
    INSERT INTO shippers (company_name, phone) VALUES
    ('Speedy Express', '(503) 555-9831'),
    ('United Package', '(503) 555-3199')
    ON CONFLICT DO NOTHING;

    -- Seed products
    INSERT INTO products (product_name, supplier_id, category_id, unit, price) VALUES
    ('Chai', 1, 1, '10 boxes x 20 bags', 18.00),
    ('Chang', 1, 1, '24 - 12 oz bottles', 19.00),
    ('Aniseed Syrup', 1, 1, '12 - 550 ml bottles', 10.00)
    ON CONFLICT DO NOTHING;

    -- Seed orders
    INSERT INTO orders (customer_id, employee_id, order_date, shipped_date, shipper_id, freight, ship_city, ship_country) VALUES
    (1, 1, '2023-01-05', '2023-01-10', 1, 29.61, 'Berlin', 'Germany'),
    (2, 2, '2023-01-12', '2023-01-14', 2, 4.39, 'México D.F.', 'Mexico')
    ON CONFLICT DO NOTHING;

    -- Seed order_details
    INSERT INTO order_details (order_id, product_id, unit_price, quantity, discount) VALUES
    (1, 1, 18.00, 12, 0),
    (1, 2, 19.00, 10, 0),
    (2, 3, 10.00, 5, 0)
    ON CONFLICT DO NOTHING;

    -- Create views
    CREATE OR REPLACE VIEW sales_by_region AS
    SELECT
        c.country AS region,
        COUNT(DISTINCT o.order_id) AS order_count,
        SUM(od.quantity * od.unit_price) AS revenue
    FROM customers c
    JOIN orders o ON c.customer_id = o.customer_id
    JOIN order_details od ON o.order_id = od.order_id
    GROUP BY c.country
    ORDER BY revenue DESC;

    CREATE OR REPLACE VIEW employee_performance AS
    SELECT
        e.first_name || ' ' || e.last_name AS employee_name,
        COUNT(DISTINCT o.order_id) AS orders_processed,
        ROUND(SUM(od.quantity * od.unit_price), 2) AS total_sales
    FROM employees e
    LEFT JOIN orders o ON e.employee_id = o.employee_id
    LEFT JOIN order_details od ON o.order_id = od.order_id
    GROUP BY e.employee_id, e.first_name, e.last_name
    ORDER BY total_sales DESC;

    SELECT 'Northwind database seeded successfully!' AS status;
EOSQL

echo "Northwind database seeding complete."
