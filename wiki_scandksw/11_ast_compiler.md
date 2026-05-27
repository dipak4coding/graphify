# AST Compiler — Expression Tree to Reverse Polish Notation

Tags: #ast #compiler #rpn
Links: [[00_INDEX]] | [[10_ast_parser]] | [[12_ast_calculator]]

---

## Purpose

The Compiler takes the **expression tree** from the Parser and converts it to a **flat list** in Reverse Polish Notation (RPN). The Calculator then evaluates this list using a stack.

```
Expression tree  →  [Compiler]  →  HPN list  →  Calculator
```

The German name in the code is **HPN** = *Heck-Polnische Notation* (Reverse Polish Notation / postfix notation).

---

## Why RPN?

RPN removes the need for parentheses and precedence rules during evaluation. Once you have RPN, a simple stack machine can evaluate any expression left-to-right:

- Push operands onto the stack
- When you see an operator, pop operands, compute, push result

For `(1 + 2) * 3`:
- Infix: `(1 + 2) * 3` — needs parentheses and precedence rules
- RPN:   `1 2 + 3 *`   — just push-and-pop

The Parser already handled all precedence and parenthesisation — the tree encodes the correct evaluation order. The Compiler just flattens it into RPN.

---

## The Conversion: Tree Traversal

The Compiler does a **post-order traversal** of the expression tree (left child → right child → operator):

```csharp
private void execute(Expression expr)
{
    if (expr is unaer)          // unary operator
    {
        unaer un = (unaer)expr;
        execute(un.Argument);   // argument first
        hpn.Add(un.Op);         // operator after
    }
    else if (expr is function)  // function like defined(), sqrt()
    {
        function fcn = (function)expr;
        execute(fcn.Argument);  // argument first
        hpn.Add(fcn.Op);        // operator after
    }
    else if (expr is binaer)    // binary operator
    {
        binaer be = (binaer)expr;
        execute(be.Left);       // left side first
        execute(be.Right);      // right side second
        hpn.Add(be.Op);         // operator after both
    }
    else if (expr is ternaer)   // ternary ?:
    {
        ternaer trnr = (ternaer)expr;
        execute(trnr.Condition);
        hpn.Add(Token.Qmark);   // separator
        execute(trnr.True);
        hpn.Add(Token.Colon);   // separator
        execute(trnr.False);
        hpn.Add(Token.Ende);    // end marker
    }
    else  // leaf nodes: variable, ASTdefine, intLiteral, doubleLiteral
    {
        hpn.Add(expr);          // push directly
    }
}
```

---

## Example Conversion

Expression: `(1 + 2) * 3`

Tree:
```
binaer(
    Left = binaer(intLiteral(1), Add, intLiteral(2)),
    Op = Mul,
    Right = intLiteral(3)
)
```

Post-order traversal:
1. Visit left: binaer(1, Add, 2) → visit left: 1 → emit intLiteral(1)
2. Visit right: 2 → emit intLiteral(2)
3. Emit Add
4. Back to root: visit right: 3 → emit intLiteral(3)
5. Emit Mul

HPN: `[intLiteral(1), intLiteral(2), Token.Add, intLiteral(3), Token.Mul]`

---

## Example: Boolean Expression

Expression: `!(CS_MODUS == 3) && FEATURE_X`

After Lexer substitution (assuming CS_MODUS defined as `"1"`):

Tree:
```
binaer(
    Left = unaer(NOT, binaer(intLiteral(1), EQ, intLiteral(3))),
    Op = AND,
    Right = variable("FEATURE_X")
)
```

HPN:
```
[intLiteral(1), intLiteral(3), Token.EQ, Token.NOT, variable("FEATURE_X"), Token.AND]
```

---

## Ternary Operator Encoding

The ternary `condition ? true_val : false_val` is encoded with special markers:

```
[condition_ops..., Token.Qmark, true_ops..., Token.Colon, false_ops..., Token.Ende]
```

The Calculator has special logic to skip the non-taken branch — see [[12_ast_calculator]] for how `L_Ternaer` works.

---

## The Compiler Does No Checking

The Compiler is a simple mechanical translation. It does NOT:
- Check types
- Simplify constant expressions
- Detect unreachable code
- Validate that operators have the right number of operands

All of that is left to the Calculator, which will throw if the stack is empty or has wrong types.

---

*Next: [[12_ast_calculator]]*
