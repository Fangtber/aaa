# -*- coding: utf-8 -*-
"""按模板生成签到表：
标题：2026年先进制造实验室设备培训签到表
信息行：培训日期：2026.10.27（周二）| 时间：14：00 | 主题：实验保养
表头：序号 | 姓名 | 备注，序号 1-26
"""
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

OUT = r"C:\Users\jiaren\Desktop\K3\P1_piweb\Pi_web\实验保养签到表_2026-10-27.xlsx"
ROWS = 26
FONT = "宋体"

wb = Workbook()
ws = wb.active
ws.title = "签到表"
ws.sheet_view.showGridLines = False

thin = Side(style="thin", color="000000")
border = Border(left=thin, right=thin, top=thin, bottom=thin)
center = Alignment(horizontal="center", vertical="center")
left_al = Alignment(horizontal="left", vertical="center", indent=1)

# 第 1 行：大标题（合并 A1:C1）
ws.merge_cells("A1:C1")
c = ws["A1"]
c.value = "2026年先进制造实验室设备培训签到表"
c.font = Font(name=FONT, size=16, bold=True)
c.alignment = center
for j in range(1, 4):
    ws.cell(row=1, column=j).border = border
ws.row_dimensions[1].height = 34

# 第 2 行：培训日期 / 时间 / 主题（合并 A2:C2，左对齐）
ws.merge_cells("A2:C2")
c = ws["A2"]
c.value = "培训日期：2026.10.27（周二） | 时间：14：00 | 主题：实验保养"
c.font = Font(name=FONT, size=11, bold=True)
c.alignment = left_al
for j in range(1, 4):
    ws.cell(row=2, column=j).border = border
ws.row_dimensions[2].height = 22

# 第 3 行：表头
headers = ["序号", "姓名", "备注"]
for j, h in enumerate(headers, start=1):
    cell = ws.cell(row=3, column=j, value=h)
    cell.font = Font(name=FONT, size=12, bold=True)
    cell.alignment = center
    cell.border = border
ws.row_dimensions[3].height = 24

# 数据行：序号 1-26 预填，姓名、备注留空
for i in range(1, ROWS + 1):
    r = i + 3
    ws.cell(row=r, column=1, value=i)
    for j in range(1, 4):
        cell = ws.cell(row=r, column=j)
        cell.font = Font(name=FONT, size=12)
        cell.alignment = center
        cell.border = border
    ws.row_dimensions[r].height = 26

# 列宽：序号窄、姓名宽、备注中（与模板版式一致）
widths = [8, 40, 18]
for j, w in enumerate(widths, start=1):
    ws.column_dimensions[get_column_letter(j)].width = w

# 打印设置：A4 纵向，水平居中
ws.page_setup.paperSize = ws.PAPERSIZE_A4
ws.page_setup.orientation = "portrait"
ws.print_options.horizontalCentered = True

wb.save(OUT)
print("saved:", OUT)
