"""一次性种子脚本：建 demo 数据库 + 写入示例数据 + 向量化结构化指标字典。

运行方式（在项目根目录）：
    python seed_data.py

会自动生成 config.DB_PATH 指向的 demo.db，以及 config.CHROMA_DIR 下的向量库。
指标与 join 声明都来自 semantics.json，本文件只负责灌数据。
"""

import sqlite3

import config
from data_sidekick import db, rag, semantics


def create_database():
    conn = sqlite3.connect(config.DB_PATH)
    conn.executescript(
        """
        DROP TABLE IF EXISTS order_items;
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS customers;

        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            name TEXT,
            city TEXT,
            signup_date TEXT
        );

        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY,
            name TEXT,
            category TEXT,
            price REAL
        );

        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            order_date TEXT,
            status TEXT,
            discount REAL
        );

        CREATE TABLE order_items (
            item_id INTEGER PRIMARY KEY,
            order_id INTEGER,
            product_id INTEGER,
            quantity INTEGER,
            unit_price REAL
        );
        """
    )

    customers = [
        (1, "Alice", "Shanghai", "2024-01-15"),
        (2, "Bob", "Beijing", "2024-02-10"),
        (3, "Carol", "Guangzhou", "2024-03-01"),
        (4, "Dave", "Shanghai", "2024-03-20"),
        (5, "Eve", "Beijing", "2024-04-05"),
        (6, "Frank", "Shenzhen", "2024-05-12"),
    ]
    products = [
        (1, "Phone", "Electronics", 5999),
        (2, "Laptop", "Electronics", 8999),
        (3, "Headphones", "Electronics", 899),
        (4, "T-shirt", "Apparel", 129),
        (5, "Coffee Maker", "Home", 499),
    ]
    # discount 是"订单级"度量（Fivetran 复制 Salesforce Order 时常见这类订单级金额字段）。
    # 它故意放在"一"侧：一旦有人 join 到 order_items 再 SUM(o.discount)，订单 1 有 2 条明细，
    # 优惠 100 就被算成 200。这正是扇出陷阱的活体样本。
    orders = [
        (1, 1, "2024-03-05", "paid", 100.0),
        (2, 1, "2024-05-10", "paid", 0.0),
        (3, 2, "2024-04-12", "paid", 50.0),
        (4, 3, "2024-06-01", "refunded", 0.0),
        (5, 4, "2024-06-15", "paid", 20.0),
        (6, 5, "2024-07-01", "paid", 0.0),
        (7, 1, "2024-07-20", "paid", 80.0),
        (8, 6, "2024-08-01", "pending", 0.0),
        (9, 2, "2024-08-10", "refunded", 0.0),
    ]
    order_items = [
        (1, 1, 1, 1, 5999),
        (2, 1, 3, 2, 899),
        (3, 2, 2, 1, 8999),
        (4, 3, 4, 3, 129),
        (5, 4, 5, 1, 499),
        (6, 5, 1, 1, 5999),
        (7, 6, 2, 1, 8999),
        (8, 7, 3, 1, 899),
        (9, 8, 4, 2, 129),
        (10, 9, 5, 1, 499),
    ]

    conn.executemany(
        "INSERT INTO customers VALUES (?, ?, ?, ?)", customers
    )
    conn.executemany(
        "INSERT INTO products VALUES (?, ?, ?, ?)", products
    )
    conn.executemany(
        "INSERT INTO orders VALUES (?, ?, ?, ?, ?)", orders
    )
    conn.executemany(
        "INSERT INTO order_items VALUES (?, ?, ?, ?, ?)", order_items
    )
    conn.commit()
    conn.close()
    print(f"数据库已创建：{config.DB_PATH}")


def seed_semantics():
    """把语义层的指标与表结构灌进向量库。

    先 reset 再灌，避免旧条目（如只有纯文本、没有结构化 payload 的历史格式）残留在检索结果里。
    表索引用于大库场景：schema 大到不能全量塞 prompt 时，按问题召回相关表。
    """
    rag.reset()
    metrics = semantics.metrics()
    rag.upsert_metrics(metrics)
    tables = db.get_schema()
    rag.upsert_schema(tables)
    print(
        f"语义层已写入向量库：{config.CHROMA_DIR}"
        f"（{len(metrics)} 个指标，{len(semantics.joins())} 条 join 声明）"
    )
    print(f"表索引已写入：{len(tables)} 张表")


def check_templates():
    """自检预写 SQL：逐条按缺省占位符试跑一遍，模板有笔误在这里就暴露。"""
    failures = semantics.verify_templates()
    if failures:
        for f in failures:
            print(f"  模板自检失败 -> {f}")
        raise SystemExit("有指标模板无法执行，请先修正 semantics.json")
    print(f"模板自检通过：{len(semantics.metrics())} 个模板均可正常执行")


if __name__ == "__main__":
    create_database()
    check_templates()
    seed_semantics()
    print("初始化完成。")