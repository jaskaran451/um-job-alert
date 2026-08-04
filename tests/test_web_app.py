import tempfile
import unittest
from pathlib import Path
from app import create_app

class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        db=Path(self.tmp.name)/'test.db'
        self.app=create_app({'TESTING':True,'DATABASE_URL':f'sqlite:///{db}','SECRET_KEY':'test-secret','DISPATCH_API_KEY':'dispatch-secret'})
        self.client=self.app.test_client()
    def tearDown(self): self.tmp.cleanup()
    def test_home_loads(self):
        response=self.client.get('/')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Create your job alert',response.data)
    def test_subscription_requires_consent(self):
        response=self.client.post('/api/subscriptions',json={'email':'student@example.com','role_types':['teaching_assistant'],'keywords':[],'consent':False})
        self.assertEqual(response.status_code,400)
        self.assertIn('consent',response.get_json()['errors'])
    def test_subscription_can_be_created(self):
        response=self.client.post('/api/subscriptions',json={'email':'Student@Example.com','role_types':['teaching_assistant'],'keywords':['COMP'],'consent':True})
        self.assertEqual(response.status_code,201)
        self.assertEqual(response.get_json()['email'],'student@example.com')
    def test_dispatch_requires_key(self):
        response=self.client.post('/api/internal/dispatch',json={'jobs':[{'id':'1','title':'Teaching Assistant','url':'https://viprecprod.ad.umanitoba.ca/DEFAULT.ASPX?REQ_ID=1'}]})
        self.assertEqual(response.status_code,401)

if __name__=='__main__': unittest.main()
