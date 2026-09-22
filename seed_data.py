"""一次性种子脚本：建 demo 数据库 + 写入示例数据 + 向量化指标字典。

运行方式（在项目根目录）：
    python seed_data.py

会自动生成 config.DB_PATH 指向的 demo.db，以及 config.CHROMA_DIR 下的向量库。
"""

import sqlite3

import config
from data_sidekick import rag

# 每条定义：(文本, 唯一 id)。文本里明确写出"口径"，供 RAG 检索 + LLM 遵守。
DEFINITIONS = [
    (
        "GMV（销售额）口径：统计 order_date 在所选时间范围内、status='paid' 的订单，"
        "其 order_items 中 quantity * unit_price 之和。status='refunded' 或 'pending' 的订单不计入 GMV。",
        "gmv",
    ),
    (
        "客单价（ARPU）口径：GMV 除以已支付订单数（status='paid' 的订单条数）。",
        "arpu",
    ),
    (
        "复购率口径：在 status='paid' 的订单中，下单次数 >= 2 的客户数 除以 "
        "至少有一次已支付订单的客户数。",
        "repurchase_rate",
    ),
    (
        "退款率口径：status='refunded' 的订单数 除以 总订单数（所有 status）。",
        "refund_rate",
    ),
    (
        "时间字段口径：orders.order_date 使用 'YYYY-MM-DD' 文本格式存储，"
        "按时间过滤时用字符串比较即可，例如 order_date >= '2024-06-01'。",
        "date_field",
    ),
]


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
            status TEXT
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
    orders = [
        (1, 1, "2024-03-05", "paid"),
        (2, 1, "2024-05-10", "paid"),
        (3, 2, "2024-04-12", "paid"),
        (4, 3, "2024-06-01", "refunded"),
        (5, 4, "2024-06-15", "paid"),
        (6, 5, "2024-07-01", "paid"),
        (7, 1, "2024-07-20", "paid"),
        (8, 6, "2024-08-01", "pending"),
        (9, 2, "2024-08-10", "refunded"),
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
        "INSERT INTO orders VALUES (?, ?, ?, ?)", orders
    )
    conn.executemany(
        "INSERT INTO order_items VALUES (?, ?, ?, ?, ?)", order_items
    )
    conn.commit()
    conn.close()
    print(f"数据库已创建：{config.DB_PATH}")


def seed_definitions():
    rag.upsert_definitions(
        texts=[d[0] for d in DEFINITIONS],
        ids=[d[1] for d in DEFINITIONS],
    )
    print(f"指标字典已写入向量库：{config.CHROMA_DIR}")


if __name__ == "__main__":
    create_database()
    seed_definitions()
    print("初始化完成。")