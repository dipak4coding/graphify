# AST Calculator — Stack Machine Evaluator

Tags: #ast #calculator #stackmachine
Links: [[00_INDEX]] | [[11_ast_compiler]] | [[07_ifdef_state_machine]]

---

## Purpose

The Calculator takes the **HPN list** (Reverse Polish Notation) from the Compiler and evaluates it to a single result — for `#if` evaluation this is always a `bool` (`true` or `false`).

```
HPN list  →  [Calculator.calc()]  →  true / false
```

---

## How a Stack Machine Works

Read the HPN list left to right:

- **Literal/variable** → push onto stack
- **Operator** → pop operands, compute, push result

Example: HPN `[1, 2, +, 3, *]`
```
Step 1: push 1  → stack: [1]
Step 2: push 2  → stack: [1, 2]
Step 3: see +   → pop 2 and 1, compute 1+2=3, push 3  → stack: [3]
Step 4: push 3  → stack: [3, 3]
Step 5: see *   → pop 3 and 3, compute 3*3=9, push 9  → stack: [9]
Result: pop stack → 9
```

---

## Implementation

```csharp
Stack<object> stack = null;

public object calc(Dictionary<string, define> a_Defines)
{
    foreach (object o in hpn)
    {
        // ... handle each token type
    }
    if (stack.Count == 0) throw new Exception("Calculator @100: Stack ist leer!");
    return stack.Pop();
}
```

---

## Token Handling

### Literals

```csharp
else if (o is intLiteral)
    stack.Push((double)intL.Value);  // cast to double for uniformity
else if (o is doubleLiteral)
    stack.Push(dblL.Value);
```

All numeric values are stored as `double` on the stack. This simplifies arithmetic.

### `variable` — Define Chain Resolution

```csharp
if (o is variable)
{
    string d0 = var.Ident;
    
    // Follow the define chain: A → B → C → "5"
    while (a_Defines.ContainsKey(d0) && a_Defines[d0].value != "")
        d0 = a_Defines[d0].value;
    
    // Try to parse as number
    bool nurDigits = true;
    // ... check if all chars are hex/decimal digits ...
    
    if (nurDigits)
        stack.Push(parseNumber(d0));  // → double
    else
        stack.Push(d0);  // push as string
}
```

The chain resolution handles cases like:
```c
#define EN_MODUS_DQ381  1
#define CS_MODUS        EN_MODUS_DQ381
// #if CS_MODUS == 1
```
Calculator resolves `CS_MODUS → "EN_MODUS_DQ381"` → `"1"` → parses as number → pushes `1.0`.

If the chain end is not numeric (e.g. some string token), it's pushed as a `string` and the `==`/`!=` operators handle string comparison.

### `ASTdefine` — `defined()` argument

```csharp
else if (o is ASTdefine)
    stack.Push(def.Name);  // push the name as a string
```

This is consumed by `Token.Defined`:
```csharp
case Token.Defined:
    if (r is string)
        stack.Push(a_Defines.ContainsKey((string)r));  // → bool
    else
        throw new Exception("Calculator: defined(): kein string als argument !");
```

So `defined(FEATURE_X)` → push `"FEATURE_X"` → pop string → `a_Defines.ContainsKey("FEATURE_X")` → push `true` or `false`.

---

## Operator Execution

```csharp
else if (o is Token)
{
    Token tok = (Token)o;
    object r = stack.Pop();    // right operand (or sole operand for unary)
    object l = 0.0;
    if (!unaerOrFunct.Contains(tok))
        l = stack.Pop();       // left operand (only for binary)
    
    switch (tok)
    {
        case Token.Add:    stack.Push((double)l + (double)r); break;
        case Token.Sub:    stack.Push((double)l - (double)r); break;
        case Token.Mul:    stack.Push((double)l * (double)r); break;
        case Token.Div:    if((double)r==0) throw; stack.Push((double)l / (double)r); break;
        case Token.NOT:    stack.Push(!objectToBool(r)); break;
        case Token.Neg:    stack.Push(-(double)r); break;
        case Token.AND:    stack.Push(objectToBool(l) && objectToBool(r)); break;
        case Token.OR:     stack.Push(objectToBool(l) || objectToBool(r)); break;
        case Token.EQ:
            if (r is string) stack.Push((string)l == (string)r);
            else if (r is double) stack.Push((double)l == (double)r);
            else if (r is bool) stack.Push(objectToBool(l) == (bool)r);
            break;
        case Token.BitAnd: stack.Push((double)(objectToUint(l) & objectToUint(r))); break;
        // ... etc.
    }
}
```

---

## Type Helpers

```csharp
bool objectToBool(object a_o)
{
    if (a_o is bool)   return (bool)a_o;
    if (a_o is int)    return ((int)a_o != 0);
    if (a_o is double) return ((double)a_o != 0.0);
    throw new Exception("Calculator: objectToBool");
}

uint objectToUint(object a_o)
{
    if (a_o is bool)   return (uint)((bool)a_o ? 1 : 0);
    if (a_o is int)    return (uint)a_o;
    if (a_o is double) { /* validates integer-representable */ return (uint)(int)a_o; }
    throw new Exception("Calculator: objectToUint");
}
```

These allow the Calculator to work with the mixed-type stack (`bool`, `double`, `string`).

---

## Ternary Operator — `L_Ternaer`

The ternary `condition ? true_val : false_val` requires **skipping the non-taken branch**. The Calculator handles this with a list of active conditions:

```csharp
List<bool> L_Ternaer = new List<bool>();

// When Token.Qmark is seen:
case Token.Qmark:
    L_Ternaer.Add(objectToBool(r));  // push condition result
    break;
```

At the start of every iteration:
```csharp
if (L_Ternaer.Count > 0)
{
    int i = L_Ternaer.Count - 1;
    if (o is Token)
    {
        if ((Token)o == Token.Colon)
        {
            L_Ternaer[i] = L_Ternaer[i] ? false : true;  // flip: after ":" evaluate the False branch
            continue;
        }
        else if ((Token)o == Token.Ende)
        {
            L_Ternaer.RemoveAt(i);  // ternary complete
            continue;
        }
    }
    
    // Check if ALL levels of L_Ternaer are satisfied
    bool T = true;
    foreach (bool L in L_Ternaer) T &= L;
    
    if (!T)
    {
        // We're in the non-taken branch — skip this token
        if ((Token)o == Token.Qmark)  // but track nested ternary!
            L_Ternaer.Add(true);  // dummy true so its :Ende will be consumed
        continue;
    }
}
```

Example: `TRUE ? 12 : 34`

HPN: `[variable(TRUE), Qmark, intLiteral(12), Colon, intLiteral(34), Ende]`

Steps:
1. `variable(TRUE)` → resolves to true → push true
2. `Qmark` → pop true → `L_Ternaer = [true]`
3. `intLiteral(12)` → L_Ternaer[0]=true → execute → push 12
4. `Colon` → flip: L_Ternaer = [false]
5. `intLiteral(34)` → L_Ternaer[0]=false → **skip**
6. `Ende` → remove from L_Ternaer
7. Stack contains: [12] → result = 12

---

## The Second `calc(double X)` Method

```csharp
public double calc(double X)
```

There is a second Calculator overload for **general math evaluation** (e.g. computing a LINEAR formula `y = factor*x + offset`). It takes a single `double` input `X` and only handles numeric operations. This is used for the A2L COMPU_METHOD evaluation, not for `#if` processing.

---

## Error Counting

In `parseIfs()`, exceptions from the Calculator are caught and counted:

```csharp
catch (Exception ex)
{
    Program.countFehler1++;   // any error
    if (ex.Message.StartsWith("Das Objekt des Typs"))  countFehler2++;  // type cast error
    if (ex.Message.StartsWith("Die angegebene Umwandlung")) countFehler3++;  // conversion error
    if (ex.Message.StartsWith("Calculator @100:"))    countFehler4++;  // empty stack
    if (ex.Message.StartsWith("Lexer: Dieser Operand")) countFehler5++; // unknown operator
    
    return true;  // assume active on failure
}
```

These counters are printed at the end of the run. In a normal well-configured run, most should be 0.

---

*Next: [[13_brute_force_search]]*
