import asyncio, concurrent.futures, unittest
import httpx
from scripts.guard import Policy, Stop, new_job, job_scope, CURRENT, admit, install, example_turn_wrapper, fingerprint_request_body

def body(value='same'):
    return {'messages':[{'role':'user','content':'task'},{'role':'tool','content':value}]}

class GuardTests(unittest.TestCase):
    def test_call_limit_and_latched_stop(self):
        p=Policy(job_calls=2);j=new_job()
        admit(j,body('1'),p);admit(j,body('2'),p)
        with self.assertRaises(Stop):admit(j,body('3'),p)
        with self.assertRaises(Stop):admit(j,body('different'),p)
        self.assertEqual(j.calls,2)
        admit(new_job(),body(),p)
    def test_time_boundary(self):
        j=new_job();p=Policy(job_seconds=10)
        admit(j,body(),p,now=j.started+9.999)
        with self.assertRaises(Stop):admit(j,body(),p,now=j.started+10)
    def test_repeat_boundary(self):
        j=new_job();p=Policy()
        for _ in range(6):admit(j,body(),p)
        with self.assertRaises(Stop):admit(j,body(),p)
        self.assertEqual(j.calls,6)
    def test_progress_shapes(self):
        for format in ['openai','responses','anthropic','gemini']:
            with self.subTest(format=format):
                j=new_job()
                for i in range(20):
                    if format=='openai':b=body(str(i))
                    elif format=='responses':b={'input':[{'role':'user','content':'task'},{'type':'function_call_output','output':str(i)}]}
                    elif format=='anthropic':b={'messages':[{'role':'user','content':'task'},{'role':'user','content':[{'type':'tool_result','content':str(i)}]}]}
                    else:b={'contents':[{'role':'user','parts':[{'text':'task'}]},{'role':'user','parts':[{'functionResponse':{'name':'tool','response':{'value':i}}}]}]}
                    admit(j,b,Policy())
    def test_tool_ids_alone_are_not_progress(self):
        a={'messages':[{'role':'user','content':[{'type':'tool_result','tool_use_id':'one','content':'same'}]}]}
        b={'messages':[{'role':'user','content':[{'type':'tool_result','tool_use_id':'two','content':'same'}]}]}
        self.assertEqual(fingerprint_request_body(a),fingerprint_request_body(b))
    def test_assistant_text_not_progress(self):
        a=body();b=body();b['messages'].append({'role':'assistant','content':'I am still thinking'})
        self.assertEqual(fingerprint_request_body(a),fingerprint_request_body(b))
    def test_scope_reuse_and_reset(self):
        j=new_job()
        with job_scope(j):
            with job_scope() as nested:self.assertIs(nested,j)
            with self.assertRaises(ValueError):
                with job_scope(new_job()):pass
        self.assertIsNone(CURRENT.get())
        with job_scope(j) as replay:self.assertIs(replay,j)
    def test_async_wrapper_holds_scope_during_await(self):
        async def inner():
            a=CURRENT.get();await asyncio.sleep(0);self.assertIs(CURRENT.get(),a);return a
        async def outer():
            a=CURRENT.get();self.assertIs(await example_turn_wrapper(inner)(),a);return a
        self.assertIsNotNone(asyncio.run(example_turn_wrapper(outer)()))
        self.assertIsNone(CURRENT.get())
    def test_sync_wrapper(self):
        self.assertIsNotNone(example_turn_wrapper(lambda:CURRENT.get())())
        self.assertIsNone(CURRENT.get())
    def test_concurrency_exact_cap(self):
        j=new_job();p=Policy(job_calls=7,identical_request_limit=100)
        def call(i):
            try:admit(j,body(str(i)),p);return 1
            except Stop:return 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:self.assertEqual(sum(pool.map(call,range(30))),7)
    def test_invalid_policy(self):
        for args in [{'job_calls':0},{'job_seconds':float('inf')},{'job_calls':True},{'identical_request_limit':-1}]:
            with self.assertRaises(ValueError):Policy(**args)
    def test_transport_sync_async_and_missing_scope(self):
        sent=[]
        def transport(req):sent.append(str(req.url));return httpx.Response(200,json={'ok':True})
        p=Policy(job_calls=2,target_hosts=('provider.example',));installation=install(p)
        try:
            self.assertIs(install(p),installation)
            with self.assertRaises(RuntimeError):install(Policy(job_calls=3,target_hosts=p.target_hosts))
            with httpx.Client(transport=httpx.MockTransport(transport)) as c:
                with self.assertRaises(Stop):c.post('https://provider.example/v1/messages',json=body())
                self.assertEqual(len(sent),0)
                c.get('https://provider.example/v1/models');c.post('https://provider.example/v1/files',json={})
                c.post('https://other.example/v1/messages',json=body())
                with job_scope() as j:
                    c.post('https://provider.example/v1/messages',json=body('1'))
                    async def run():
                        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as a:
                            await a.post('https://provider.example/v1/messages',json=body('2'))
                            with self.assertRaises(Stop):await a.post('https://provider.example/v1/messages',json=body('3'))
                    asyncio.run(run());self.assertEqual(j.calls,2)
                with job_scope(j):
                    with self.assertRaises(Stop):c.post('https://provider.example/v1/messages',json=body('new'))
                self.assertEqual(len(sent),5)
        finally:installation.close()

if __name__=='__main__':unittest.main()
