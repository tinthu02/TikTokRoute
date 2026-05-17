import sqlite3

# tạo hoặc mở database
conn = sqlite3.connect("user_preferences.db")

# tạo con trỏ thao tác database
cursor = conn.cursor()

# tạo bảng preferences
cursor.execute("""
CREATE TABLE IF NOT EXISTS preferences (
    user_id INTEGER,
    category TEXT,
    weight REAL
)
""")

# lưu thay đổi
conn.commit()

# đóng database
conn.close()

print("Database created successfully!")