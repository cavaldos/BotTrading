import json
from pathlib import Path
from datetime import datetime
from typing import Optional


class JsonDB:
    """Quản lý file JSON đơn giản."""
    
    def __init__(self, file_path: str):
        self.file = Path(file_path)
        self._data = None
    
    @property
    def data(self) -> list:
        """Lazy load data."""
        if self._data is None:
            self._load()
        return self._data
    
    def _load(self):
        """Đọc file."""
        if self.file.exists():
            try:
                with open(self.file, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
            except:
                self._data = []
        else:
            self._data = []
    
    def save(self):
        """Ghi file."""
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
    
    def add(self, item: dict):
        """Thêm item."""
        self.data.append(item)
        return self
    
    def find(self, **kwargs) -> list:
        """Tìm theo điều kiện."""
        return [
            item for item in self.data
            if all(item.get(k) == v for k, v in kwargs.items())
        ]
    
    def find_one(self, **kwargs) -> Optional[dict]:
        """Tìm 1 item."""
        results = self.find(**kwargs)
        return results[0] if results else None
    
    def update(self, query: dict, updates: dict) -> int:
        """Cập nhật items khớp điều kiện."""
        count = 0
        for item in self.data:
            if all(item.get(k) == v for k, v in query.items()):
                item.update(updates)
                count += 1
        return count
    
    def delete(self, **kwargs) -> int:
        """Xóa items khớp điều kiện."""
        before = len(self.data)
        self._data = [
            item for item in self.data
            if not all(item.get(k) == v for k, v in kwargs.items())
        ]
        return before - len(self._data)
    
    def all(self) -> list:
        """Lấy tất cả."""
        return self.data
    
    def count(self) -> int:
        """Đếm số lượng."""
        return len(self.data)
    
    def clear(self):
        """Xóa tất cả."""
        self._data = []
        return self
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.save()

if __name__ == "__main__":
    db = JsonDB('test_db.json')
    db.add({'id': 1, 'name': 'Alice', 'created_at': datetime.now().isoformat()})
    db.add({'id': 2, 'name': 'Bob', 'created_at': datetime.now().isoformat()})
    db.save()
    print(db.all())