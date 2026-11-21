// Parse static curriculum data without executing the source application.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const ts = require('typescript');
const root = process.argv[2];
if (!root) throw new Error('Usage: node curriculum.cjs REFERENCE_ROOT');
const inputs = [
 ['course1', 'app/api/course1/toc/route.ts', ['COURSE1_TOC']],
 ['course2', 'app/course2/learn/page.tsx', ['COURSE2_TOC']],
 ['course3', 'app/api/course3/toc-data.ts', ['COURSE3_SEMESTER1','COURSE3_SEMESTER2']],
];
const units = [];
for (const [course, filename, names] of inputs) {
 const text = fs.readFileSync(path.join(root, filename), 'utf8');
 const tree = ts.createSourceFile(filename,text,ts.ScriptTarget.Latest,true);
 const declarations = new Map();
 function visit(n) {
  if (ts.isVariableDeclaration(n) && ts.isIdentifier(n.name) && n.initializer)
   declarations.set(n.name.text,n.initializer);
  ts.forEachChild(n,visit);
 }
 visit(tree);
 function value(n,seen=new Set()) {
  if (!n) throw new Error('Missing static initializer');
  if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) return n.text;
  if (ts.isNumericLiteral(n)) return Number(n.text);
  if (ts.isArrayLiteralExpression(n)) return n.elements.map(x=>value(x,seen));
  if (ts.isObjectLiteralExpression(n)) return Object.fromEntries(n.properties.map(p=>{
   if (!ts.isPropertyAssignment(p)) throw new Error('Nonliteral property');
   return [p.name.text,value(p.initializer,seen)];
  }));
  if (ts.isBinaryExpression(n) && n.operatorToken.kind===ts.SyntaxKind.PlusToken)
   return value(n.left,seen)+value(n.right,seen);
  if (ts.isAsExpression(n) || ts.isParenthesizedExpression(n)) return value(n.expression,seen);
  if (ts.isIdentifier(n) && declarations.has(n.text) && !seen.has(n.text))
   return value(declarations.get(n.text),new Set([...seen,n.text]));
  throw new Error('Unsupported static syntax: '+ts.SyntaxKind[n.kind]);
 }
 function walk(node,trail,parents=[]) {
  if (Array.isArray(node)) { for (const item of node) walk(item,trail,parents); return; }
  if (!node || typeof node!=='object') return;
  const childArrays=Object.entries(node).filter(([k,v])=>Array.isArray(v));
  if (node.id && node.title && (node.contentKey || !childArrays.length)) {
   const sourceId=[course,...trail,node.id].join('/');
   units.push({source_topic_id:sourceId,unit_id:'unit-'+crypto.createHash('sha256').update(sourceId).digest('hex').slice(0,20),
    title:node.title,content_key:node.contentKey||node.title,parents,source_file:filename,
    source_hash:crypto.createHash('sha256').update(text).digest('hex'),track_ids:[],mapping_status:'source_mapped'});
  }
  for (const [key,children] of childArrays) walk(children,[...trail,key],node.title?[...parents,node.title]:parents);
 }
 for (const name of names) walk(value(declarations.get(name)),[name]);
}
if (new Set(units.map(u=>u.source_topic_id)).size!==units.length) throw new Error('Duplicate source IDs');
process.stdout.write(JSON.stringify({version:'source-curriculum-v1',units},null,2)+'\n');
