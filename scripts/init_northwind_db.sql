-- Create northwind database and seed with sample data
-- This runs during postgres initialization

-- Create the northwind database if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'northwind') THEN
        CREATE DATABASE northwind;
    END IF;
END $$;
