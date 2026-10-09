import gspread as gs

client = gs.service_account(filename='credentials.json')
sheet_file_name = "bom_dataset"
spreadsheet = client.open(sheet_file_name)

sheets_target = str(input())

sheet_list = []

for x in sheets_target.split(","):
    x = x.strip()
    sheet_list.append(x)

all_sheets = spreadsheet.worksheets()
sheet_names = [sheet.title for sheet in all_sheets]

print()
