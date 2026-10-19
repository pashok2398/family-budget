export async function api<T>(path:string, init:RequestInit = {}):Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('X-Family-Budget','local');
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type','application/json');
  const response = await fetch('/api'+path,{...init,headers});
  if (!response.ok) {
    const error = await response.json().catch(()=>({detail:'שגיאת תקשורת עם השרת'}));
    throw new Error(typeof error.detail === 'string' ? error.detail : 'יש לבדוק את הנתונים שהוזנו');
  }
  return response.json();
}
export const send = <T>(path:string, body:unknown, method='POST') => api<T>(path,{method,body:JSON.stringify(body)});
