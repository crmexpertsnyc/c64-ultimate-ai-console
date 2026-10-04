// 📟 PETSCII screen + keyboard mapping. Run: node --test tests/  (Node 22.6+ strips the TypeScript types)
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  COLS, LOWER_SET, PetsciiScreen, ROWS, UPPER_SET, decodeCp437, encodeCp437, keyToPetscii, petsciiToScreen, textToPetscii,
} from '../src/services/petscii.ts'

const bytes = (...xs: (number | string)[]) =>
  new Uint8Array(xs.flatMap((x) => (typeof x === 'string' ? [...x].map((c) => c.charCodeAt(0)) : [x])))
const key = (k: string, mods: Partial<{ shiftKey: boolean; ctrlKey: boolean }> = {}) =>
  keyToPetscii({ key: k, shiftKey: false, ctrlKey: false, altKey: false, metaKey: false, ...mods })

test('PETSCII → screen codes', () => {
  assert.equal(petsciiToScreen(0x41), 0x01)       // A
  assert.equal(petsciiToScreen(0xc1), 0x41)       // shifted A
  assert.equal(petsciiToScreen(0x61), 0x41)
  assert.equal(petsciiToScreen(0xa0), 0x60)       // shifted space
  assert.equal(petsciiToScreen(0xe1), 0x61)
  assert.equal(petsciiToScreen(0xff), 0x5e)       // π
  assert.equal(petsciiToScreen(0x0d), -1)
  assert.equal(petsciiToScreen(0x93), -1)
})

test('both character sets', () => {
  assert.equal(UPPER_SET[0x01], 'A')
  assert.equal(LOWER_SET[0x01], 'a')
  assert.equal(LOWER_SET[0x41], 'A')
  assert.equal(UPPER_SET[0x41], '♠')
  assert.equal(UPPER_SET[0x5e], 'π')
  assert.equal(UPPER_SET[0x1c], '£')
  assert.equal(LOWER_SET[0x7a], '✓')
  assert.equal(UPPER_SET.length, 128)
})

test('text, case switching, colours and reverse video', () => {
  const s = new PetsciiScreen()
  s.write(bytes(0x93, 0x0e, 'HELLO ', 0xc3, '64', 0x0d))
  assert.equal(s.text(), 'hello C64')
  s.write(bytes(0x8e))
  assert.equal(s.text(), `HELLO ${String.fromCodePoint(0x1fb78)}64`)   // same screen codes, upper case / graphics set
  s.write(bytes(0x93, 0x1c, 'R', 0x12, 'V', 0x92, 'N'))
  assert.equal(s.colors[0], 2)                         // red
  assert.equal(s.chars[0], 0x12)                       // R
  assert.equal(s.chars[1], 0x16 | 0x80)                // V reversed
  assert.equal(s.chars[2], 0x0e)
  s.write(bytes(0x0d, 'X'))
  assert.equal(s.chars[COLS] & 0x80, 0)                // RETURN turns reverse off
})

test('cursor movement, home, clear, insert and delete', () => {
  const s = new PetsciiScreen()
  s.lower = false
  s.write(bytes(0x93, 'ABCD', 0x9d, 0x9d, 0x14))       // left, left, DEL → removes B
  assert.equal(s.text(), 'ACD')
  s.write(bytes(0x94, 'Z'))                            // INST then Z where C was
  assert.equal(s.text(), 'AZCD')
  s.write(bytes(0x13, 0x11, 0x1d, '!'))                // home, down, right
  assert.equal(s.text(), 'AZCD\n !')
  s.write(bytes(0x91, 0x91, '^'))                      // up beyond the top stays on row 0
  assert.equal(s.y, 0)
  s.write(bytes(0x93))
  assert.equal(s.text(), '')
  assert.deepEqual([s.x, s.y], [0, 0])
})

test('wrapping and scrolling', () => {
  const s = new PetsciiScreen()
  s.lower = false
  s.write(bytes(0x93, 'X'.repeat(COLS), 'Y'))
  assert.equal(s.y, 1)
  for (let i = 0; i < ROWS; i++) s.write(bytes(`L${i}`, 0x0d))
  assert.equal(s.y, ROWS - 1)
  assert.ok(s.text().endsWith(`L${ROWS - 1}`))
  assert.ok(!s.text().includes('XXXX'))                // scrolled off the top
})

test('keyboard → PETSCII', () => {
  assert.deepEqual(key('a'), [0x41])
  assert.deepEqual(key('A', { shiftKey: true }), [0xc1])
  assert.deepEqual(key('Enter'), [0x0d])
  assert.deepEqual(key('Backspace'), [0x14])
  assert.deepEqual(key('ArrowUp'), [0x91])
  assert.deepEqual(key('Home', { shiftKey: true }), [0x93])
  assert.deepEqual(key('F1'), [0x85])
  assert.deepEqual(key('F8'), [0x8c])
  assert.deepEqual(key('Escape'), [0x03])
  assert.deepEqual(key('1', { ctrlKey: true }), [0x90])
  assert.deepEqual(key('9', { ctrlKey: true }), [0x12])
  assert.equal(key('Tab'), null)
  assert.deepEqual(textToPetscii('Hi 64!'), [0xc8, 0x49, 0x20, 0x36, 0x34, 0x21])
  assert.equal(textToPetscii('é'), null)
})

test('CP437 for ANSI boards', () => {
  assert.equal(decodeCp437(bytes(0xc9, 0xcd, 0xbb, 'A', 0xb0)), '╔═╗A░')
  assert.deepEqual([...encodeCp437('a╔\x7f€')], [0x61, 0xc9, 0x08, 0x3f])
})
