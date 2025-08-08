import gspread

gc = gspread.oauth()

sh = gc.open("Budget Aug25").sheet1

print(sh.cell(20, 4))