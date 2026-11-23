INSERT INTO products (sku, name, category, weight_kg, size_cm, color, description, ycb_object, robot_pickable) VALUES
    ('SKU-1001', 'Mustard Bottle',      'grocery',  0.60, '{9.6,5.8,19.1}',   'yellow',
     'yellow plastic squeeze bottle of mustard with a red cap', '006_mustard_bottle', true),
    ('SKU-1002', 'Tomato Soup Can',     'grocery',  0.35, '{6.8,6.8,10.2}',   'red',
     'small red and white cylindrical can of tomato soup', '005_tomato_soup_can', true),
    ('SKU-2001', 'Cracker Box',         'grocery',  0.41, '{16.4,7.2,21.3}',  'red',
     'tall red box of cheese crackers', '003_cracker_box', false),
    ('SKU-2002', 'Sugar Box',           'grocery',  0.51, '{9.3,4.5,17.6}',   'white',
     'white and yellow box of sugar', '004_sugar_box', false),
    ('SKU-2003', 'Chocolate Pudding',   'grocery',  0.19, '{11.4,3.8,9.0}',   'brown',
     'small brown box of chocolate pudding mix', '008_pudding_box', false),
    ('SKU-2004', 'Strawberry Gelatin',  'grocery',  0.10, '{8.9,3.0,7.3}',    'red',
     'small red box of strawberry gelatin', '009_gelatin_box', false),
    ('SKU-2005', 'Potted Meat Can',     'grocery',  0.37, '{10.2,5.8,8.4}',   'blue',
     'blue rectangular can of potted meat', '010_potted_meat_can', false),
    ('SKU-2006', 'Coffee Can',          'grocery',  0.41, '{10.2,10.2,14.0}', 'blue',
     'large blue cylindrical can of ground coffee', '002_master_chef_can', false),
    ('SKU-3001', 'Bleach Cleanser',     'cleaning', 1.13, '{10.2,6.8,25.1}',  'white',
     'white plastic bottle of bleach cleanser with a blue label', '021_bleach_cleanser', false),
    ('SKU-4001', 'Ceramic Mug',         'home',     0.12, '{11.7,9.3,8.1}',   'red',
     'red ceramic coffee mug', '025_mug', false),
    ('SKU-5001', 'Wood Block',          'toys',     0.73, '{9.0,9.1,20.6}',   'beige',
     'tall unpainted wooden block', '036_wood_block', false),
    ('SKU-5002', 'Foam Brick',          'toys',     0.03, '{7.8,5.3,5.1}',    'red',
     'small red foam brick', '061_foam_brick', false);

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
    ('SKU-2005', 'A-01-1', 3),
    ('SKU-1002', 'A-02-1', 4),
    ('SKU-1001', 'A-03-1', 2),
    ('SKU-1002', 'A-03-1', 2),
    ('SKU-1001', 'B-02-1', 2),
    ('SKU-2003', 'B-02-1', 4),
    ('SKU-1002', 'B-03-1', 3),
    ('SKU-1002', 'C-01-1', 3),
    ('SKU-2004', 'C-01-1', 4),
    ('SKU-1001', 'C-03-2', 2),
    ('SKU-2001', 'A-01-2', 4),
    ('SKU-3001', 'A-02-2', 3),
    ('SKU-2002', 'A-03-2', 4),
    ('SKU-2006', 'A-04-1', 3),
    ('SKU-5002', 'A-04-1', 5),
    ('SKU-4001', 'A-04-2', 3),
    ('SKU-5001', 'B-01-2', 3),
    ('SKU-2001', 'B-02-2', 3),
    ('SKU-2004', 'B-02-2', 3),
    ('SKU-2006', 'B-04-1', 2),
    ('SKU-2005', 'B-04-1', 4),
    ('SKU-3001', 'B-04-2', 2),
    ('SKU-2002', 'C-01-2', 3),
    ('SKU-5002', 'C-01-2', 4),
    ('SKU-2003', 'C-02-2', 5),
    ('SKU-4001', 'C-03-1', 4),
    ('SKU-5001', 'C-04-2', 2),
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
