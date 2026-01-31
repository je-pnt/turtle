import sqlite3

conn = sqlite3.connect('nova/data/nova_truth.db')
cursor = conn.cursor()

# Check tables
cursor.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = [r[0] for r in cursor.fetchall()]
print('Tables:', tables)

if 'commandEvents' in tables:
    # Count command events
    cursor.execute('SELECT COUNT(*) FROM commandEvents')
    count = cursor.fetchone()[0]
    print(f'\nTotal command events: {count}')
    
    # Show recent commands
    cursor.execute('''
        SELECT messageType, commandId, commandType, targetId, timelineMode
        FROM commandEvents 
        ORDER BY sourceTruthTime DESC 
        LIMIT 10
    ''')
    
    print('\nRecent command events:')
    for row in cursor.fetchall():
        msgType, cmdId, cmdType, target, mode = row
        print(f'  {msgType:20s} {cmdType:20s} → {target:25s} [{mode}]')
else:
    print('\n✗ commandEvents table not found!')

conn.close()
