# -*- coding: utf-8 -*-
import csv
from datetime import datetime

def esc(v):
    if v is None:
        return 'NULL'
    return "'" + str(v).replace('\\', '\\\\').replace("'", "''") + "'"

def read_csv(path):
    with open(path, encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))

sales = read_csv(r'F:\11\sales.csv')
stores = read_csv(r'F:\11\stores.csv')
products = read_csv(r'F:\11\products.csv')

store_ids = {r['store_id'].strip() for r in stores}
prod_ids = {r['product_id'].strip() for r in products}
prod_price = {r['product_id'].strip(): float(r['unit_price']) for r in products}

stats = {'raw': len(sales), 'dup_removed': 0, 'store_fixed': 0, 'date_fixed': 0,
         'amount_filled': 0, 'yen_stripped': 0, 'rejected': 0}

def norm_date(s):
    s = s.strip()
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d'), fmt != '%Y-%m-%d'
        except ValueError:
            pass
    return None, False

clean_rows, rejects, seen_full, seen_oid = [], [], set(), set()
for r in sales:
    oid = r['order_id'].strip()
    sid_raw = r['store_id'].strip()
    pid = r['product_id'].strip()
    sid = sid_raw.upper()
    if sid != sid_raw:
        stats['store_fixed'] += 1
    date, fixed = norm_date(r['date'])
    if fixed:
        stats['date_fixed'] += 1
    qty = r['qty'].strip()
    amt_raw = r['amount'].strip()
    reason = None
    if amt_raw.startswith('\u00a5'):
        amt_raw = amt_raw.lstrip('\u00a5')
        stats['yen_stripped'] += 1
    try:
        amt = round(float(amt_raw), 2) if amt_raw else None
    except ValueError:
        amt = None
        reason = 'amount无法解析'
    try:
        q = int(qty)
    except ValueError:
        q = None
        reason = 'qty无法解析'
    if reason is None and q is not None and q <= 0:
        reason = 'qty非法(' + qty + ')'
    if reason is None and date is None:
        reason = '日期非法(' + r['date'].strip() + ')'
    if reason is None and sid not in store_ids:
        reason = '门店外键不存在(' + sid_raw + ')'
    if reason is None and pid not in prod_ids:
        reason = '商品外键不存在(' + pid + ')'
    if reason is None and amt is None:
        amt = round(q * prod_price[pid], 2)
        stats['amount_filled'] += 1

    key = (oid, date, sid, pid, q, amt, r['payment'].strip())
    if reason is None:
        if key in seen_full:
            stats['dup_removed'] += 1
            continue
        if oid in seen_oid:
            reason = '订单号冲突(重复订单号' + oid + ')'
        else:
            seen_full.add(key)
            seen_oid.add(oid)
    if reason:
        stats['rejected'] += 1
        rejects.append((oid, r['date'].strip(), sid_raw, pid, qty,
                        r['amount'].strip(), r['payment'].strip(), reason))
        continue
    clean_rows.append((oid, date, sid, pid, q, format(amt, '.2f'), r['payment'].strip()))

print('原始 %d 行 -> 清洗后 %d 行' % (stats['raw'], len(clean_rows)))
print('整行重复删除: %d, s01规范化: %d, 日期格式修复: %d' % (
    stats['dup_removed'], stats['store_fixed'], stats['date_fixed']))
print('yen前缀修复: %d, 金额补齐(qty*单价): %d, 剔除: %d' % (
    stats['yen_stripped'], stats['amount_filled'], stats['rejected']))

DDL = """
SET NAMES utf8mb4;
DROP DATABASE IF EXISTS Demo;
CREATE DATABASE Demo CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE Demo;

CREATE TABLE stores (
  store_id  VARCHAR(10)  NOT NULL COMMENT '门店ID',
  store_name VARCHAR(50) NOT NULL COMMENT '门店名称',
  category  VARCHAR(20)  COMMENT '品类',
  district  VARCHAR(50)  COMMENT '区域',
  PRIMARY KEY (store_id)
) ENGINE=InnoDB COMMENT='门店维表';

CREATE TABLE products (
  product_id   VARCHAR(10)  NOT NULL COMMENT '商品ID',
  product_name VARCHAR(50)  NOT NULL COMMENT '商品名称',
  product_category VARCHAR(20) COMMENT '商品品类',
  unit_price   DECIMAL(10,2) NOT NULL COMMENT '单价',
  PRIMARY KEY (product_id)
) ENGINE=InnoDB COMMENT='商品维表';

CREATE TABLE sales_raw (
  id      BIGINT AUTO_INCREMENT PRIMARY KEY,
  order_id   VARCHAR(20),
  date       VARCHAR(20),
  store_id   VARCHAR(10),
  product_id VARCHAR(10),
  qty        VARCHAR(10),
  amount     VARCHAR(20),
  payment    VARCHAR(20)
) ENGINE=InnoDB COMMENT='销售流水原始留档(未清洗)';

CREATE TABLE sales (
  id      BIGINT AUTO_INCREMENT PRIMARY KEY,
  order_id   VARCHAR(20) NOT NULL COMMENT '订单号',
  date       DATE        NOT NULL COMMENT '日期',
  store_id   VARCHAR(10) NOT NULL COMMENT '门店外键->stores',
  product_id VARCHAR(10) NOT NULL COMMENT '商品外键->products',
  qty        INT         NOT NULL COMMENT '数量',
  amount     DECIMAL(10,2) COMMENT '金额(缺失时按qty*单价补齐)',
  payment    VARCHAR(20) COMMENT '支付方式',
  KEY idx_date (date),
  KEY idx_store (store_id),
  KEY idx_product (product_id),
  KEY idx_order (order_id)
) ENGINE=InnoDB COMMENT='销售流水(清洗后)';

CREATE TABLE sales_rejects (
  id      BIGINT AUTO_INCREMENT PRIMARY KEY,
  order_id VARCHAR(20), date_raw VARCHAR(20), store_id_raw VARCHAR(10),
  product_id_raw VARCHAR(10), qty_raw VARCHAR(10), amount_raw VARCHAR(20),
  payment VARCHAR(20), reject_reason VARCHAR(100)
) ENGINE=InnoDB COMMENT='清洗剔除记录(留痕)';

CREATE VIEW v_sales_detail AS
SELECT s.order_id, s.date, s.store_id, st.store_name, st.category AS store_category,
       st.district, s.product_id, p.product_name, p.product_category, p.unit_price,
       s.qty, s.amount, s.payment
FROM sales s
JOIN stores st ON st.store_id = s.store_id
JOIN products p ON p.product_id = s.product_id;
"""

out = [DDL]

def batch(table, cols, rows, size=500):
    for i in range(0, len(rows), size):
        vals = ",\n".join("(" + ", ".join(esc(v) for v in row) + ")" for row in rows[i:i+size])
        out.append("INSERT INTO " + table + " (" + ', '.join(cols) + ") VALUES\n" + vals + ";")

batch('stores', ['store_id', 'store_name', 'category', 'district'],
      [(r['store_id'].strip(), r['store_name'].strip(), r['category'].strip(), r['district'].strip()) for r in stores])
batch('products', ['product_id', 'product_name', 'product_category', 'unit_price'],
      [(r['product_id'].strip(), r['product_name'].strip(), r['product_category'].strip(), r['unit_price'].strip()) for r in products])
raw = [(r['order_id'].strip(), r['date'].strip(), r['store_id'].strip(), r['product_id'].strip(),
        r['qty'].strip(), r['amount'].strip(), r['payment'].strip()) for r in sales]
batch('sales_raw', ['order_id', 'date', 'store_id', 'product_id', 'qty', 'amount', 'payment'], raw)
batch('sales', ['order_id', 'date', 'store_id', 'product_id', 'qty', 'amount', 'payment'], clean_rows)
batch('sales_rejects', ['order_id', 'date_raw', 'store_id_raw', 'product_id_raw', 'qty_raw',
                        'amount_raw', 'payment', 'reject_reason'], rejects)

with open(r'F:\11\import_demo.sql', 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(out))
print('SQL 已生成: F:\\11\\import_demo.sql')
