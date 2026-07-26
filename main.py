from fastapi import FastAPI
from routers import expense

app = FastAPI()
app.include_router(expense.router)

@app.get("/")
def read_root():
    return {"message": "Hello from ai-expense-tracker-fastapi!"}
