INSERT INTO products (sku, name, category, weight_kg, size_cm, color, description, ycb_object) VALUES
    ('SKU-1001', 'Mustard Bottle',  'grocery', 0.60, '{9.6,5.8,19.1}',  'yellow',
     'yellow plastic squeeze bottle of mustard with a red cap', '006_mustard_bottle'),
    ('SKU-1002', 'Tomato Soup Can', 'grocery', 0.35, '{6.8,6.8,10.2}',  'red',
     'small red and white cylindrical can of tomato soup', '005_tomato_soup_can');

INSERT INTO locations (id, kind, aisle, bay, level, x, y, z)
SELECT format('%s-%s-%s', a.aisle, lpad(b.bay::text, 2, '0'), l.level),
       'storage', a.aisle, b.bay, l.level,
       a.x, (b.bay * 1.5)::numeric, (0.3 + (l.level - 1) * 0.45)::numeric
FROM (VALUES ('A', 2.0), ('B', 5.0), ('C', 8.0)) AS a(aisle, x)
CROSS JOIN generate_series(1, 4) AS b(bay)
CROSS JOIN generate_series(1, 2) AS l(level);

INSERT INTO locations (id, kind, x, y) VALUES
    ('DISPATCH-1', 'dispatch', 12.0, 2.0),
    ('DISPATCH-2', 'dispatch', 12.0, 5.0),
    ('STAGING-1',  'staging',  12.0, 8.0),
    ('DOCK-1',     'charging', 0.0, 0.0);

INSERT INTO inventory (sku, location_id, quantity) VALUES
    ('SKU-1001', 'A-01-1', 3),
    ('SKU-1001', 'B-02-1', 2),
    ('SKU-1001', 'C-03-2', 2),
    ('SKU-1001', 'A-03-1', 2),
    ('SKU-1002', 'A-03-1', 2),
    ('SKU-1002', 'A-02-1', 4),
    ('SKU-1002', 'B-03-1', 3),
    ('SKU-1002', 'C-01-1', 3),
    ('SKU-1001', 'STAGING-1', 4),
    ('SKU-1002', 'STAGING-1', 4);

INSERT INTO orders (id, customer, priority, dispatch_location) VALUES
    (1040, 'Northwind Retail',  3, 'DISPATCH-1'),
    (1041, 'Contoso Office',    2, 'DISPATCH-2'),
    (1042, 'Acme Fitness',      1, 'DISPATCH-1'),
    (1043, 'Fabrikam Home',     3, 'DISPATCH-2'),
    (1044, 'Tailspin Grocers',  2, 'DISPATCH-1');

INSERT INTO order_lines (order_id, sku, quantity) VALUES
    (1040, 'SKU-1001', 1),
    (1040, 'SKU-1002', 2),
    (1041, 'SKU-1002', 1),
    (1042, 'SKU-1001', 2),
    (1042, 'SKU-1002', 1),
    (1043, 'SKU-1001', 1),
    (1044, 'SKU-1001', 8);

INSERT INTO robots (id, name, model, x, y) VALUES
    ('amr-01', 'Picker 1', 'Ridgeback-Franka', 0.0, 0.0);
