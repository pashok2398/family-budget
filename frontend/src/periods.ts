const label=(d:Date)=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
// A financial period is labelled by the month it starts in and runs to the day before the same start day next month.
export const monthNow=(startDay=1,d=new Date())=>{return label(d.getDate()>=startDay?d:new Date(d.getFullYear(),d.getMonth()-1,1))};
export const periodRange=(month:string,startDay:number)=>{const [y,m]=month.split('-').map(Number);const f=(d:Date)=>`${String(d.getDate()).padStart(2,'0')}.${String(d.getMonth()+1).padStart(2,'0')}`;return `${f(new Date(y,m-1,startDay))}–${f(new Date(y,m,startDay-1))}`};
