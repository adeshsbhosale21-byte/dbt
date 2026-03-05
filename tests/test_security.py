from security import apply_guardrails

def test_guardrails():
    # Safe cases
    safe_cases = [
        "What are the models available?",
        "Show me the first 5 records of fct_sales",
        "SELECT * FROM fct_sales LIMIT 10",
        "How many rows in dim_users?"
    ]
    
    # Destructive cases (Input)
    destructive_input = [
        "drop table fct_sales",
        "Drop the table fct_sales",
        "DROP   TABLE fct_sales",
        "Can you truncate table orders?",
        "delete from users where 1=1",
        "alter table fct_sales add column secret",
        "drop database production",
        "insert into log values ('hacked')"
    ]
    
    # Tool Argument cases (JSON strings as passed in main.py)
    destructive_args = [
        '[{"name": "show", "args": {"sql_query": "DROP TABLE fct_sales"}}]',
        '[{"name": "show", "args": {"sql_query": "DELETE FROM users"}}]'
    ]
    
    print("--- Testing Safe Cases ---")
    for s in safe_cases:
        res = apply_guardrails(s, "input")
        print(f"PASS" if res else f"FAIL (Blocked)", f"-> '{s}'")
        assert res == True

    print("\n--- Testing Destructive Input ---")
    for d in destructive_input:
        res = apply_guardrails(d, "input")
        print(f"PASS (Blocked)" if not res else f"FAIL (Allowed)", f"-> '{d}'")
        assert res == False

    print("\n--- Testing Destructive Tool Args ---")
    for a in destructive_args:
        res = apply_guardrails(a, "input")
        print(f"PASS (Blocked)" if not res else f"FAIL (Allowed)", f"-> '{a}'")
        assert res == False

    print("\nAll security tests passed!")

if __name__ == "__main__":
    test_guardrails()
