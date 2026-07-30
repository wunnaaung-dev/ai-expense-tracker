
from typing import List

from schemas.expense import BatchResult, ExpenseData
from services.notion.abstract_storage_strategy import StorageStrategy
from services.storage.backup_storage import BackupStorage


class ResilientStorageService(StorageStrategy):
    def __init__(self, primary: StorageStrategy, backup: BackupStorage):
        self.primary = primary
        self.backup = backup
        
    async def insert_many(self, items: List[ExpenseData]) -> List[BatchResult]:
        try:
            results = await self.primary.insert_many(items)
        except Exception as e:
            # If the primary storage fails, save to backup
            for item in items:
                await self.backup.save(item)
            raise e 
        
        final_results = []
        for result in results:
            if result.status == "failed" and result.data is not None:
                await self.backup.save(result.data)
                result.error = f"{result.error} Data saved to backup."
            final_results.append(result)
            
        return final_results
    
    async def sync_backup(self) -> List[BatchResult]:
        pending_items = await self.backup.load_all()
        if not pending_items:
            return []
        
        results = await self.primary.insert_many(pending_items)
        
        failed_items = [result.data for result in results if result.status == "failed" and result.data]

        await self.backup.clear()  # Clear backup after attempting to sync  
        for item in failed_items:
            await self.backup.save(item)  # Save failed items back to backup
        return results