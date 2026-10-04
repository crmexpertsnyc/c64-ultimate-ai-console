import sqlite3
import zipfile


def test_backup_and_rotation(app_client, tmp_path):
    dest = tmp_path / "backups"
    with app_client(BACKUP_DIR=str(dest), BACKUP_KEEP=2) as c:
        cont = c.app.state.container
        data = cont.settings.data_path
        (data / "savestates").mkdir(parents=True, exist_ok=True)
        (data / "savestates" / "7.state").write_bytes(b"save")
        (data / "assembly64").mkdir(exist_ok=True)
        (data / "assembly64" / "cache.d64").write_bytes(b"x" * 100)        # re-downloadable: not backed up
        m = sqlite3.connect(data / "magazines.db")
        m.execute("create table t (x)")
        m.commit()
        m.close()
        first = cont.backup.run()
        assert first["full"]                                                # the first backup is a full one
        with zipfile.ZipFile(dest / first["backup"]) as z:
            names = set(z.namelist())
        assert {"c64console.db", "magazines.db", "savestates/7.state", "README.txt"} <= names
        assert not any(n.startswith("assembly64/") for n in names)
        with zipfile.ZipFile(dest / first["backup"]) as z:
            (tmp_path / "x.db").write_bytes(z.read("c64console.db"))
        restored = sqlite3.connect(tmp_path / "x.db")
        assert restored.execute("select count(*) from sqlite_master").fetchone()[0] > 5     # a real, complete database
        restored.close()
        # daily backups (no magazine index) rotate: only the newest BACKUP_KEEP stay, full ones are kept apart
        import time
        for _ in range(3):
            time.sleep(1.1)
            r = cont.backup.run(full=False)
            assert not r["full"]
        st = c.get("/api/backups").json()
        assert [b["full"] for b in st["backups"]].count(False) == 2 and [b["full"] for b in st["backups"]].count(True) == 1
        assert "backup" in {j["key"] for j in c.get("/api/updates").json()["jobs"]}
