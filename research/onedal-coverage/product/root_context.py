"""Probe each rejected installed header with GCC and the matching oneDAL source."""
import csv
import subprocess
import sys
from pathlib import Path

inc = Path(sys.argv[1]) / 'include'
source = Path(sys.argv[2]) / 'cpp'
with open('root-context.csv', 'w') as stream:
    writer = csv.writer(stream)
    writer.writerow(['root', 'context', 'exit', 'first_error'])
    for row in csv.DictReader(open('unparsed.csv')):
        for label, extra in [('package', []), ('source', ['-I'+str(source)]),
                             ('source-ide', ['-I'+str(source), '-D__ONEDAL_IDE_MODE__'])]:
            result = subprocess.run(['g++', '-std=c++17', '-fsyntax-only', '-x', 'c++',
                                     '-I'+str(inc), '-I'+str(inc/'dal'), *extra, '-'],
                                    input=f"#include <{row['root']}>\n", capture_output=True, text=True)
            error = next((line.split('error: ', 1)[1] for line in result.stderr.splitlines()
                          if 'error: ' in line), '')
            writer.writerow([row['root'], label, result.returncode, error])
