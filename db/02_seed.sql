INSERT INTO products (sku, name, category, weight_kg, size_cm, color) VALUES
    ('SKU-1001', 'Wireless Mouse',        'electronics', 0.30, '{15,10,6}',  'red'),
    ('SKU-1002', 'USB-C Hub',             'electronics', 0.25, '{14,9,4}',   'blue'),
    ('SKU-1003', 'Mechanical Keyboard',   'electronics', 1.10, '{45,15,6}',  'black'),
    ('SKU-1004', 'Noise-Cancel Headset',  'electronics', 0.60, '{22,20,10}', 'white'),
    ('SKU-2001', 'Ceramic Mug Set',       'home',        1.40, '{25,20,12}', 'yellow'),
    ('SKU-2002', 'LED Desk Lamp',         'home',        0.90, '{30,15,15}', 'green'),
    ('SKU-2003', 'Cotton Towel Pack',     'home',        0.80, '{30,25,10}', 'orange'),
    ('SKU-3001', 'Protein Bar Box',       'grocery',     0.70, '{20,15,8}',  'purple'),
    ('SKU-3002', 'Green Tea Tin',         'grocery',     0.30, '{10,10,15}', 'teal'),
    ('SKU-4001', 'Yoga Block',            'sports',      0.40, '{23,15,8}',  'pink'),
    ('SKU-4002', 'Resistance Band Kit',   'sports',      0.50, '{20,15,6}',  'gray'),
    ('SKU-4003', 'Water Bottle',          'sports',      0.35, '{25,8,8}',   'navy');

INSERT INTO locations (id, kind, aisle, bay, level, x, y, z)
SELECT format('%s-%s-%s', a.aisle, lpad(b.bay::text, 2, '0'), l.level),
       'storage', a.aisle, b.bay, l.level,
       a.x, (b.bay * 1.5)::numeric, (0.4 + (l.level - 1) * 0.5)::numeric
FROM (VALUES ('A', 2.0), ('B', 5.0), ('C', 8.0)) AS a(aisle, x)
CROSS JOIN generate_series(1, 4) AS b(bay)
CROSS JOIN generate_series(1, 2) AS l(level);

INSERT INTO locations (id, kind, x, y) VALUES
    ('DISPATCH-1', 'dispatch', 12.0, 2.0),
    ('DISPATCH-2', 'dispatch', 12.0, 5.0),
    ('STAGING-1',  'staging',  12.0, 8.0),
    ('DOCK-1',     'charging', 0.0, 0.0);

INSERT INTO inventory (sku, location_id, quantity) VALUES
    ('SKU-1001', 'A-01-1', 12),
    ('SKU-1002', 'A-01-2', 20),
    ('SKU-1003', 'A-02-1', 6),
    ('SKU-1004', 'A-03-1', 4),
    ('SKU-2001', 'B-01-1', 8),
    ('SKU-2002', 'B-02-1', 10),
    ('SKU-2002', 'C-04-2', 3),
    ('SKU-2003', 'B-03-2', 15),
    ('SKU-3001', 'C-01-1', 25),
    ('SKU-3002', 'C-02-1', 2),
    ('SKU-4001', 'C-03-1', 9),
    ('SKU-4002', 'B-04-1', 7),
    ('SKU-4003', 'A-04-2', 14),
    ('SKU-1001', 'B-01-2', 6),
    ('SKU-1003', 'C-02-2', 4),
    ('SKU-2001', 'A-02-2', 5),
    ('SKU-3001', 'B-02-2', 10),
    ('SKU-4001', 'A-03-2', 6),
    ('SKU-4003', 'C-03-2', 8);

INSERT INTO orders (id, customer, priority, dispatch_location) VALUES
    (1040, 'Northwind Retail',  3, 'DISPATCH-1'),
    (1041, 'Contoso Office',    2, 'DISPATCH-2'),
    (1042, 'Acme Fitness',      1, 'DISPATCH-1'),
    (1043, 'Fabrikam Home',     3, 'DISPATCH-2'),
    (1044, 'Tailspin Grocers',  2, 'DISPATCH-1');

INSERT INTO order_lines (order_id, sku, quantity) VALUES
    (1040, 'SKU-1001', 2),
    (1040, 'SKU-1002', 1),
    (1041, 'SKU-1003', 1),
    (1041, 'SKU-2002', 1),
    (1042, 'SKU-4001', 2),
    (1042, 'SKU-4003', 1),
    (1042, 'SKU-3001', 1),
    (1043, 'SKU-2001', 1),
    (1043, 'SKU-2003', 2),
    (1044, 'SKU-3002', 3),
    (1044, 'SKU-3001', 2);

INSERT INTO robots (id, name, model, x, y) VALUES
    ('amr-01', 'Picker 1', 'Ridgeback-Franka', 0.0, 0.0);
