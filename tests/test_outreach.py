import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app as app_module
from db import Base, engine
from models.outreach_prospect import OutreachProspect


class OutreachFlowTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.create_all(bind=engine)
        with Session(engine) as session:
            session.query(OutreachProspect).delete()
            session.commit()

    def test_bulk_outreach_creates_prospect_and_activity(self):
        with patch.object(app_module, "send_email", return_value=None):
            with TestClient(app_module.app) as client:
                response = client.post(
                    "/dashboard/outreach/run",
                    data={"targets": "https://example.com\nExample Plumbing"},
                    follow_redirects=False,
                )

        self.assertEqual(response.status_code, 303)
        with Session(engine) as session:
            prospects = session.query(OutreachProspect).all()
            self.assertGreaterEqual(len(prospects), 1)
            prospect = prospects[0]
            self.assertEqual(prospect.status, "outreach_pending")
            self.assertGreaterEqual(len(prospect.activities), 1)


if __name__ == "__main__":
    unittest.main()
