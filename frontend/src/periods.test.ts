import {describe,expect,it} from 'vitest';
import {monthNow,periodRange} from './periods';

describe('financial periods',()=>{
 it('labels a period by the month it starts in',()=>{
  expect(monthNow(10,new Date(2026,9,10))).toBe('2026-10');
  expect(monthNow(10,new Date(2026,10,9))).toBe('2026-10');
  expect(monthNow(10,new Date(2026,9,9))).toBe('2026-09');
  expect(monthNow(10,new Date(2026,0,5))).toBe('2025-12');
  expect(monthNow(1,new Date(2026,9,1))).toBe('2026-10');
 });
 it('shows the inclusive date range',()=>{
  expect(periodRange('2026-10',10)).toBe('10.10–09.11');
  expect(periodRange('2026-12',10)).toBe('10.12–09.01');
  expect(periodRange('2026-02',1)).toBe('01.02–28.02');
 });
});
