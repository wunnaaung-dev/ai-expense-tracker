import aiofiles
from pathlib import Path
from typing import List
from schemas.expense import ExpenseData

class BackupStorage:
    def __init__(self, user_id: int):
        self.backup_file = Path(f"data/backup/{user_id}.jsonl")
        self.backup_file.parent.mkdir(parents=True, exist_ok=True)
        
    async def save(self, item: ExpenseData) -> None:
        async with aiofiles.open(self.backup_file, mode="a") as f:
            await f.write(item.model_dump_json() + "\n")
            
    async def load_all(self) -> List[ExpenseData]:
        if not self.backup_file.exists():
            return []
        items = []
        async with aiofiles.open(self.backup_file, mode="r") as f:
            async for line in f:
                if line.strip():
                    items.append(ExpenseData.model_validate_json(line.strip()))
        return items
    
    async def clear(self) -> None:
        if self.backup_file.exists():
            self.backup_file.unlink()
