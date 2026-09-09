-- Northwind sample database for local development and testing.
-- Seeded with realistic data matching the sample reports in test-assets/sample_reports/.

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

-- Seed customers (sample of real Northwind-style data)
INSERT INTO customers (company_name, contact_name, contact_title, city, region, postal_code, country, phone) VALUES
('Alfreds Futterkiste', 'Maria Anders', 'Sales Representative', 'Berlin', NULL, '12209', 'Germany', '+49 30 0074321'),
('Ana Trujillo Emparedados y helados', 'Ana Trujillo', 'Owner', 'México D.F.', NULL, '05021', 'Mexico', '(5) 555-4729'),
('Antonio Moreno Taquería', 'Antonio Moreno', 'Owner', 'México D.F.', NULL, '05023', 'Mexico', '(5) 555-3932'),
('Around the Horn', 'Thomas Hardy', 'Sales Representative', 'London', NULL, 'WX1 6LT', 'UK', '(171) 555-7788'),
('Berglunds snabbköp', 'Christina Berglund', 'Export Manager', 'Luleå', NULL, 'S-958 12', 'Sweden', '0921-12 34 65'),
('Blauer See Delikatessen', 'Hanna Moos', 'Sales Representative', 'Mannheim', NULL, '68306', 'Germany', '+49 621 08460'),
('Bólido Comidas preparadas', 'Martín Sommer', 'Owner', 'Madrid', NULL, '28023', 'Spain', '(91) 555 22 82'),
('Centro comercial Moctezuma', 'Francisco Chang', 'Marketing Manager', 'México D.F.', NULL, '05024', 'Mexico', '(5) 555-3392'),
('Chop-suey Chinese', 'Yang Wang', 'Owner', 'Bern', NULL, '3012', 'Switzerland', '+49 452 1600'),
('Comércio Mineiro', 'Pedro Afonso', 'Sales Associate', 'São Paulo', 'SP', '05432-043', 'Brazil', '(11) 555-7647')
ON CONFLICT DO NOTHING;

-- Seed employees
INSERT INTO employees (first_name, last_name, title, city, country, hire_date) VALUES
('Nancy', 'Davolio', 'Sales Representative', 'Seattle', 'USA', '1992-05-01'),
('Andrew', 'Fuller', 'Vice President, Sales', 'Tacoma', 'USA', '1992-08-14'),
('Janet', 'Leverling', 'Sales Representative', 'Kirkland', 'USA', '1992-04-01'),
('Margaret', 'Peacock', 'Sales Representative', 'Redmond', 'USA', '1993-05-03'),
('Steven', 'Buchanan', 'Sales Manager', 'London', 'UK', '1993-11-17'),
('Michael', 'Suyama', 'Sales Representative', 'London', 'UK', '1993-01-02'),
('Robert', 'King', 'Sales Representative', 'London', 'UK', '1994-05-01'),
('Laura', 'Callahan', 'Inside Sales Coordinator', 'Seattle', 'USA', '1994-03-05'),
('Anne', 'Dodsworth', 'Sales Representative', 'London', 'UK', '1994-11-15');

-- Seed categories
INSERT INTO categories (category_name, description) VALUES
('Beverages', 'Soft drinks, coffees, teas, beers, and ales'),
('Condiments', 'Sweet and savory sauces, relishes, spreads, and seasonings'),
('Confections', 'Desserts, candies, and sweet breads'),
('Dairy Products', 'Cheeses and milk products'),
('Grains/Cereals', 'Breads, crackers, pasta, and cereal'),
('Meat/Poultry', 'Beef, pork, chicken, and seafood'),
('Produce', 'Dried fruit and bean curd'),
('Seafood', 'Seaweed and fish');

-- Seed suppliers
INSERT INTO suppliers (company_name, contact_name, city, country) VALUES
('Exotic Liquids', 'Charlotte Cooper', 'London', 'UK'),
('New Orleans Cajun Delights', 'Shelley Burke', 'New Orleans', 'USA'),
('Grandma Kelly''s Homestead', 'Regina Murphy', 'San Francisco', 'USA'),
('Tokyo Traders', 'Yoshi Nagase', 'Tokyo', 'Japan'),
('Cooperativa de Quesos ''Las Cabras''', 'Antonio del Valle Saavedra', 'Oviedo', 'Spain');

-- Seed shippers
INSERT INTO shippers (company_name, phone) VALUES
('Speedy Express', '(503) 555-9831'),
('United Package', '(503) 555-3199'),
('Federal Shipping', '(503) 555-9931');

-- Seed products
INSERT INTO products (product_name, supplier_id, category_id, unit, price) VALUES
('Chai', 1, 1, '10 boxes x 20 bags', 18.00),
('Chang', 1, 1, '24 - 12 oz bottles', 19.00),
('Aniseed Syrup', 1, 1, '12 - 550 ml bottles', 10.00),
('Chef Anton''s Cajun Seasoning', 2, 2, '48 - 6 oz jars', 22.00),
('Chef Anton''s Gumbo Mix', 2, 2, '36 boxes', 21.35),
('Grandma''s Boysenberry Spread', 3, 2, '12 - 8 oz jars', 25.00),
('Uncle Bob''s Organic Dried Pears', 3, 7, '12 - 1 lb pkgs.', 30.00),
('Northwoods Cranberry Sauce', 3, 2, '12 - 12 oz jars', 40.00),
('Mishi Kobe Niku', 4, 6, '10 boxes x 8 pieces', 97.00),
('Ikura', 4, 8, '12 boxes', 31.00),
('Longlife Tofu', 5, 6, '50 bags', 10.00),
('Manjimup Dried Apples', 3, 7, '50 - 300 g pkgs.', 53.00),
('Filet de Bœuf', 7, 6, '12 slices', 263.50),
('Sir Rodney''s Scones', 7, 3, '24 pkgs. x 4 pieces', 10.00),
('Gustaf''s Knäckebröd', 7, 5, '24 - 500 g boxes', 21.00),
('Tunnbröd', 7, 5, '12 - 250 g pkgs.', 9.00),
('Gula Malacca', 1, 7, '20 - 2 kg bags', 19.45),
('Rössle Sauerkraut', 8, 2, '25 - 825 g cans', 45.60),
('Northwoods Cranberry Sauce', 3, 2, '12 - 12 oz jars', 40.00),
('Jack''s New England Clam Chowder', 9, 8, '12 - 12 oz cans', 9.65),
('Lakkalikööri', 1, 1, '500 ml', 18.00),
('Longlife Tofu', 5, 6, '50 bags', 10.00);

-- Seed orders
INSERT INTO orders (customer_id, employee_id, order_date, required_date, shipped_date, shipper_id, freight, ship_city, ship_country) VALUES
(1, 5, '2023-01-05', '2023-02-05', '2023-01-10', 1, 29.61, 'Berlin', 'Germany'),
(2, 3, '2023-01-12', '2023-02-12', '2023-01-14', 2, 4.39, 'México D.F.', 'Mexico'),
(3, 1, '2023-01-25', '2023-02-25', '2023-01-30', 3, 20.12, 'México D.F.', 'Mexico'),
(4, 8, '2023-02-03', '2023-03-03', '2023-02-07', 1, 50.96, 'London', 'UK'),
(5, 2, '2023-02-12', '2023-03-12', '2023-02-14', 2, 65.83, 'Luleå', 'Sweden'),
(6, 6, '2023-02-20', '2023-03-20', '2023-02-25', 3, 47.42, 'Mannheim', 'Germany'),
(7, 4, '2023-03-01', '2023-04-01', '2023-03-05', 1, 88.40, 'Madrid', 'Spain'),
(8, 7, '2023-03-10', '2023-04-10', '2023-03-15', 2, 13.97, 'México D.F.', 'Mexico'),
(9, 9, '2023-03-18', '2023-04-18', '2023-03-22', 3, 44.78, 'Bern', 'Switzerland'),
(10, 5, '2023-04-01', '2023-05-01', '2023-04-05', 1, 32.38, 'São Paulo', 'Brazil');

-- Seed order details
INSERT INTO order_details (order_id, product_id, unit_price, quantity, discount) VALUES
(1, 1, 18.00, 12, 0),
(1, 2, 19.00, 10, 0),
(1, 42, 14.00, 5, 0),
(2, 14, 23.25, 9, 0),
(2, 51, 53.00, 40, 0),
(3, 40, 18.40, 10, 0),
(3, 59, 44.00, 6, 0),
(3, 72, 34.80, 10, 0),
(4, 23, 9.00, 30, 0),
(4, 24, 3.60, 10, 0),
(5, 17, 39.00, 15, 0),
(5, 19, 9.20, 12, 0),
(5, 34, 13.60, 20, 0),
(5, 60, 34.00, 15, 0),
(6, 1, 18.00, 20, 0),
(6, 2, 19.00, 12, 0),
(7, 4, 22.00, 25, 0),
(7, 6, 25.00, 30, 0),
(8, 11, 21.00, 10, 0),
(8, 43, 46.00, 25, 0),
(9, 1, 18.00, 15, 0),
(9, 7, 30.00, 10, 0),
(10, 21, 10.00, 20, 0),
(10, 65, 22.00, 15, 0);

-- Create useful views for reporting
CREATE OR REPLACE VIEW sales_by_region AS
SELECT
    c.country AS region,
    COUNT(DISTINCT o.order_id) AS order_count,
    SUM(od.quantity * od.unit_price * (1 - od.discount/100)) AS revenue,
    ROUND(AVG(od.unit_price), 2) AS avg_unit_price
FROM customers c
JOIN orders o ON c.customer_id = o.customer_id
JOIN order_details od ON o.order_id = od.order_id
GROUP BY c.country
ORDER BY revenue DESC;

CREATE OR REPLACE VIEW employee_performance AS
SELECT
    e.first_name || ' ' || e.last_name AS employee_name,
    COUNT(DISTINCT o.order_id) AS orders_processed,
    ROUND(SUM(od.quantity * od.unit_price * (1 - od.discount/100)), 2) AS total_sales,
    ROUND(AVG(od.unit_price), 2) AS avg_item_price
FROM employees e
LEFT JOIN orders o ON e.employee_id = o.employee_id
LEFT JOIN order_details od ON o.order_id = od.order_id
GROUP BY e.employee_id, e.first_name, e.last_name
ORDER BY total_sales DESC NULLS LAST;

CREATE OR REPLACE VIEW product_sales_summary AS
SELECT
    p.product_name,
    c.category_name AS product_category,
    SUM(od.quantity) AS total_quantity_sold,
    ROUND(SUM(od.quantity * od.unit_price * (1 - od.discount/100)), 2) AS revenue,
    COUNT(DISTINCT o.order_id) AS order_count
FROM products p
JOIN categories c ON p.category_id = c.category_id
LEFT JOIN order_details od ON p.product_id = od.product_id
LEFT JOIN orders o ON od.order_id = o.order_id
GROUP BY p.product_id, p.product_name, c.category_name
ORDER BY revenue DESC;
