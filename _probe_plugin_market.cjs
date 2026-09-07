
const { DatabaseSync } = require('node:sqlite');
const db = new DatabaseSync(process.env.USERPROFILE + '/.dsh/plugin-market.db', { readOnly: true });
const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").all();
console.log('TABLES:', tables.map(t => t.name).join(', '));
for (const t of tables) {
  try {
    const rows = db.prepare('SELECT * FROM ' + t.name + ' LIMIT 10').all();
    // 打印含 memory/hindsight/recall 的行
    const s = JSON.stringify(rows);
    if (s.match(/memory|hindsight|recall|knowledge/i)) {
      console.log('=== ' + t.name + ' has memory refs ===');
      for (const r of rows) {
        const rs = JSON.stringify(r);
        if (rs.match(/memory|hindsight|recall|knowledge/i)) console.log(rs.slice(0, 400));
      }
    } else {
      console.log(t.name + ':', rows.length, 'rows (no memory ref)');
    }
  } catch (e) { console.log(t.name, 'err', e.message); }
}
