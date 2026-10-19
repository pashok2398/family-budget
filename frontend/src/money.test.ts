import {describe,it,expect} from 'vitest';
import {cents,amount,percent} from './money';
describe('exact financial display',()=>{
 it('preserves cents',()=>expect(cents('364.87')+cents('207.72')).toBe(57259n));
 it('formats signed values',()=>expect(amount('-0.01')).toBe('−0.01 ₪'));
 it('rejects invalid decimals',()=>expect(()=>cents('1.001')).toThrow());
 it('caps display progress',()=>{expect(percent('300','100')).toBe(100);expect(percent('-10','100')).toBe(0)});
});
