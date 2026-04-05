import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aiosqlite

from app.galileo.database import (
    append_agent_message,
    finalize_agent_run,
    init_galileo_db,
    mark_agent_dialing,
    promote_next_queued_agent,
    record_agent_quote,
)


class GalileoLiveSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "negotiations.db"
        self.db_patch = patch("app.galileo.database.DB_PATH", self.db_path)
        self.db_patch.start()
        await init_galileo_db()
        await self._seed_agent()

    async def asyncTearDown(self) -> None:
        self.db_patch.stop()
        self._tmpdir.cleanup()

    async def _seed_agent(self) -> None:
        async with aiosqlite.connect(str(self.db_path)) as conn:
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute(
                """
                INSERT INTO galileo_enterprises (id, name, description)
                VALUES ('ent_test', 'Test Enterprise', 'Test enterprise')
                """
            )
            await conn.execute(
                """
                INSERT INTO galileo_companies (id, name, industry, badge)
                VALUES ('cmp_test', 'Test Hotel', 'Hotel', 'Preferred Supplier')
                """
            )
            await conn.execute(
                """
                INSERT INTO galileo_events (
                    id, enterprise_id, name, location, start_date, end_date, attendees, service, status
                )
                VALUES ('evt_test', 'ent_test', 'Test Event', 'Chicago, IL', '2026-09-10', '2026-09-12', 10, 'Hotel', 'Active')
                """
            )
            await conn.execute(
                """
                INSERT INTO galileo_agents (
                    id, enterprise_id, event_id, company_name, company_id, status,
                    ideal_price, ceiling_price, market_price, current_price, is_accepted
                )
                VALUES ('agt_test', 'ent_test', 'evt_test', 'Test Hotel', 'cmp_test', 'Negotiating', 180, 240, 250, 245, 0)
                """
            )
            await conn.execute(
                """
                INSERT INTO galileo_agents (
                    id, enterprise_id, event_id, company_name, company_id, status,
                    ideal_price, ceiling_price, market_price, current_price, is_accepted
                )
                VALUES ('agt_queued', 'ent_test', 'evt_test', 'Queued Hotel', 'cmp_test', 'Queued', 175, 235, 245, 240, 0)
                """
            )
            await conn.commit()

    async def test_live_sync_writes_messages_quotes_and_completion(self) -> None:
        await mark_agent_dialing("agt_test", "CA123")
        await append_agent_message("agt_test", "We can do 229 with breakfast.", "Rep")
        await append_agent_message("agt_test", "Can you confirm parking?", "Galileo")
        await record_agent_quote("agt_test", 229.0, rate_type="corporate")
        await finalize_agent_run("agt_test", "FAILED", best_rate=229.0)

        async with aiosqlite.connect(str(self.db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT status, outcome, current_price, is_accepted FROM galileo_agents WHERE id = 'agt_test'"
            )
            agent = await cursor.fetchone()
            await cursor.close()
            self.assertEqual(agent["status"], "Completed")
            self.assertEqual(agent["outcome"], "FAILED")
            self.assertEqual(float(agent["current_price"]), 229.0)
            self.assertEqual(int(agent["is_accepted"]), 0)

            message_rows = await conn.execute_fetchall(
                "SELECT sender, message FROM galileo_messages WHERE agent_id = 'agt_test' ORDER BY rowid ASC"
            )
            self.assertEqual(len(message_rows), 2)
            self.assertEqual(message_rows[0]["sender"], "Rep")
            self.assertEqual(message_rows[1]["sender"], "Galileo")

            price_rows = await conn.execute_fetchall(
                "SELECT label, price, type FROM galileo_price_points WHERE agent_id = 'agt_test' ORDER BY rowid ASC"
            )
            self.assertEqual(len(price_rows), 1)
            self.assertEqual(price_rows[0]["label"], "Live Quote (corporate)")
            self.assertEqual(float(price_rows[0]["price"]), 229.0)

            cursor = await conn.execute(
                """
                SELECT badge, detail, active
                FROM galileo_activity_stream
                WHERE agent_id = 'agt_test'
                ORDER BY rowid DESC
                LIMIT 1
                """
            )
            latest_activity = await cursor.fetchone()
            await cursor.close()
            self.assertEqual(latest_activity["badge"], "Failed")
            self.assertEqual(int(latest_activity["active"]), 1)

    async def test_promote_next_queued_agent_after_completion(self) -> None:
        await finalize_agent_run("agt_test", "FAILED", best_rate=229.0)

        promoted = await promote_next_queued_agent("evt_test")
        self.assertIsNotNone(promoted)
        self.assertEqual(promoted["id"], "agt_queued")
        self.assertEqual(promoted["status"], "Negotiating")

        async with aiosqlite.connect(str(self.db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT status, outcome, is_accepted FROM galileo_agents WHERE id = 'agt_queued'"
            )
            agent = await cursor.fetchone()
            await cursor.close()
            self.assertEqual(agent["status"], "Negotiating")
            self.assertIsNone(agent["outcome"])
            self.assertEqual(int(agent["is_accepted"]), 0)


if __name__ == "__main__":
    unittest.main()
