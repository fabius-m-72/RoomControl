import httpx
class ShellyHTTP:
 def __init__(self,base_url,auth=None,timeout=3.0): self.base=base_url.rstrip('/'); self.auth=auth; self.timeout=timeout
 async def set_relay(self,channel,on:bool)->bool:
  url=f'{self.base}/rpc/Switch.Set'
  async with httpx.AsyncClient(timeout=self.timeout,auth=self.auth) as c:
   r=await c.post(url,json={'id':channel,'on':on}); return r.status_code==200
 async def pulse(self,channel,ms:int=800)->bool:
  url=f'{self.base}/rpc/Switch.Set'
  async with httpx.AsyncClient(timeout=self.timeout,auth=self.auth) as c:
   r=await c.post(url,json={'id':channel,'on':True,'toggle_after':ms/1000}); return r.status_code==200
