import http.client
import json
import threading
import unittest

from geomeditor.server import EditorServer


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.server = EditorServer(('127.0.0.1',0))
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection('127.0.0.1',self.server.server_port)

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self,path,body=None,token=True,headers=None):
        hs=dict(headers or {})
        if token:hs['X-GeomEditor-Token']=self.server.token
        self.connection.request('POST' if body is not None else 'GET',path,body=body,headers=hs)
        response=self.connection.getresponse()
        return response.status,dict(response.getheaders()),response.read()

    def op(self,**params):
        return self.request('/api/operation',json.dumps(dict(revision=self.server.document.revision,**params)))

    def test_html_assets_and_local_only_policy(self):
        for path in ['/','/app.js','/viewer.js','/style.css']:
            status,headers,data=self.request(path)
            self.assertEqual(status,200)
            self.assertGreater(len(data),100)
            self.assertIn("default-src 'self'",headers['Content-Security-Policy'])
        self.assertEqual(self.request('/api/state',headers={'Host':'attacker.example'})[0],403)
        self.assertEqual(self.request('/../requirements.txt')[0],404)

    def test_wrong_origin_and_token_rejected(self):
        data=json.dumps({'action':'sample','name':'tank','revision':0})
        self.assertEqual(self.request('/api/operation',data,token=False)[0],403)
        self.assertEqual(self.request('/api/operation',data,headers={'Origin':'https://other.example'})[0],403)
        self.assertEqual(self.server.document.revision,0)

    def test_http_import_edit_export_project_workflow(self):
        code,_,data=self.op(action='sample',name='tank')
        self.assertEqual(code,200)
        code,_,data=self.op(action='split_plane',bodies=['B1'],origin=[0,0,30],normal=[0,0,1])
        self.assertEqual(code,200)
        self.assertEqual(json.loads(data)['bodies'][0]['solids'],2)
        code,headers,step=self.request('/api/export?format=step')
        self.assertEqual(code,200)
        self.assertIn(b'ISO-10303-21',step)
        self.assertIn('geometry.step',headers['Content-Disposition'])
        code,_,data=self.request('/api/import?name=roundtrip.step',step)
        self.assertEqual(code,200)
        self.assertEqual(len(json.loads(data)['bodies']),2)
        code,_,project=self.request('/api/project')
        self.assertEqual(code,200)
        code,_,data=self.request('/api/project',project)
        self.assertEqual(code,200)
        self.assertEqual(len(json.loads(data)['bodies']),2)

    def test_bad_operation_returns_error_without_mutation(self):
        self.op(action='sample',name='tank')
        old=self.server.document.revision
        code,_,data=self.op(action='split_plane',bodies=['B1'],origin=[0,0,0],normal=[0,0,0])
        self.assertEqual(code,422)
        self.assertIn('error',json.loads(data))
        self.assertEqual(self.server.document.revision,old)
        self.assertEqual(self.request('/api/operation',b'[]')[0],422)


if __name__=='__main__':unittest.main()
