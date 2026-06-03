with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Tìm dòng conflict còn lại
start_line = None
end_line = None
sep_line = None

for i, line in enumerate(lines):
    if '<<<<<<< HEAD' in line and i > 490 and i < 510:
        start_line = i
    if '=======' in line and start_line and i > start_line and sep_line is None:
        sep_line = i
    if '>>>>>>> origin/main' in line and sep_line and i > sep_line:
        end_line = i
        break

print(f'Conflict: dong {start_line+1} den {end_line+1}')
print(f'HEAD: dong {start_line+2} den {sep_line}')
print(f'MAIN: dong {sep_line+2} den {end_line}')

# Giu ca 2 phan: HEAD truoc, MAIN sau (merge ca 2 vi khong xung dot logic)
head_lines = lines[start_line+1:sep_line]
main_lines = lines[sep_line+1:end_line]

# Xoa dong conflict, giu ca 2 phan
new_lines = lines[:start_line] + head_lines + main_lines + lines[end_line+1:]

with open('app.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print('Fixed! Giu ca HEAD va MAIN (khong xung dot logic, chi la them CSS)')

# Kiem tra con conflict khong
with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
found = [(i+1, l.strip()) for i, l in enumerate(lines) if '<<<<<<<' in l or '>>>>>>>' in l]
print('Con conflict:', found if found else 'Sach roi!')
