import re
import os

tex_path = '/Users/bekiryan/Projects/GLDtk/docs/thesis/latex/main.tex'
bib_path = '/Users/bekiryan/Projects/GLDtk/docs/thesis/latex/references.bib'
base_dir = '/Users/bekiryan/Projects/GLDtk/docs/thesis/latex'

with open(tex_path, 'r', encoding='utf-8') as f:
    tex = f.read()

with open(bib_path, 'r', encoding='utf-8') as f:
    bib = f.read()

# 1. Check BibTeX keys
bib_keys = set(re.findall(r'@\w+\s*\{\s*([^,]+),', bib))
print(f'Total BibTeX keys in references.bib: {len(bib_keys)}')

cites = set()
for m in re.finditer(r'\\cite(?:[pt]|author|year)?\{([^}]+)\}', tex):
    for k in m.group(1).split(','):
        cites.add(k.strip())

print(f'Total cited keys in main.tex: {len(cites)}')
missing_cites = cites - bib_keys
if missing_cites:
    print('ERROR: Missing cite keys in references.bib:', missing_cites)
else:
    print('SUCCESS: All cite keys exist in references.bib!')

# 2. Check \includegraphics
graphics = re.findall(r'\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}', tex)
print(f'Total includegraphics calls: {len(graphics)}')
missing_files = []
for g in graphics:
    full_path = os.path.join(base_dir, g)
    if not os.path.isfile(full_path):
        if os.path.isfile(full_path + '.png') or os.path.isfile(full_path + '.jpg'):
            pass
        else:
            missing_files.append((g, full_path))

if missing_files:
    print('ERROR: Missing image files:', missing_files)
else:
    print('SUCCESS: All includegraphics files exist on disk!')

# 3. Check balanced environments
lines = tex.splitlines()
env_stack = []
env_errors = []
for line_no, line in enumerate(lines, 1):
    # strip true comments: ignore escaped \%
    chars = []
    i = 0
    while i < len(line):
        if line[i] == '\\' and i + 1 < len(line):
            chars.append(line[i:i+2])
            i += 2
        elif line[i] == '%':
            break
        else:
            chars.append(line[i])
            i += 1
    code_part = ''.join(chars)

    begins = re.findall(r'\\begin\{([^}]+)\}', code_part)
    ends = re.findall(r'\\end\{([^}]+)\}', code_part)
    for b in begins:
        env_stack.append((b, line_no))
    for e in ends:
        if not env_stack:
            env_errors.append(f'Unmatched \\end{{{e}}} at line {line_no}')
        else:
            top_b, top_line = env_stack.pop()
            if top_b != e:
                env_errors.append(f'Mismatched environment: \\begin{{{top_b}}} at {top_line} vs \\end{{{e}}} at {line_no}')

if env_stack:
    for b, l in env_stack:
        env_errors.append(f'Unclosed \\begin{{{b}}} from line {l}')

if env_errors:
    print('ERROR in environments:')
    for err in env_errors:
        print('  ', err)
else:
    print('SUCCESS: All LaTeX environments are perfectly matched and balanced!')

# 4. Check balanced braces
brace_count = 0
brace_errors = []
for line_no, line in enumerate(lines, 1):
    chars = []
    i = 0
    while i < len(line):
        if line[i] == '\\' and i + 1 < len(line):
            # Escaped character (e.g. \{, \}, \%, etc.) - skip both
            i += 2
        elif line[i] == '%':
            break
        else:
            chars.append(line[i])
            i += 1
    code_part = ''.join(chars)
    for ch in code_part:
        if ch == '{':
            brace_count += 1
        elif ch == '}':
            brace_count -= 1
            if brace_count < 0:
                brace_errors.append(f'Negative brace count at line {line_no}')
                brace_count = 0

if brace_count != 0:
    brace_errors.append(f'Unbalanced braces: final count = {brace_count}')

if brace_errors:
    print('ERROR in braces:')
    for err in brace_errors:
        print('  ', err)
else:
    print('SUCCESS: All curly braces outside comments are perfectly balanced!')
