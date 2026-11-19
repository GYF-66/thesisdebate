const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const React = require('react')
const {renderToStaticMarkup} = require('react-dom/server')
const {transformSync} = require('next/dist/build/swc')
function compile(file, requireModule = require) {
  const filename = path.join(__dirname, '..', file)
  const code = transformSync(fs.readFileSync(filename, 'utf8'), {filename, jsc:{parser:{syntax:'typescript',tsx:file.endsWith('.tsx')},target:'es2020',transform:{react:{runtime:'automatic'}}},module:{type:'commonjs'}}).code
  const module={exports:{}}
  vm.runInNewContext(code,{module,exports:module.exports,require:requireModule})
  return module.exports
}
const lib=compile('lib/changeGrouping.ts')
const change=(id,start=0,extra={})=>({id,title:'标题前空行',detail:'依据',before:'',after:'新增空行',kind:'format',status:'pending',anchor:'# 标题',rule:'heading.spacing',start,end:start,replacement:'\n',...extra})
const many=Array.from({length:130},(_,i)=>change('c'+i,i*10))
assert.equal(lib.groupChanges(many).length,1)
assert.equal(lib.groupChanges(many)[0].locations.length,130)
const duplicate=lib.groupChanges([change('a',0),change('b',0,{rule:'paragraph.spacing',title:'另一来源'})])
assert.equal(duplicate.length,1)
assert.equal(duplicate[0].locations.length,1)
assert.deepEqual(Array.from(duplicate[0].items,x=>x.id),['a','b'])
assert.equal(lib.conflictingChangeIds(duplicate[0].items).size,0)
const competing=[change('a',0),change('b',0,{replacement:'\n\n'})]
assert.equal(lib.conflictingChangeIds(competing).size,2)
assert.equal(lib.conflictingChangeIds([change('a',0,{end:10}),change('b',5,{end:15})]).size,2)
const source='😀开场\n# 重复\n正文\n# 重复\n结尾'
const first=source.indexOf('# 重复'), second=source.lastIndexOf('# 重复')
assert.equal(lib.changeLocation(source,first,'','标题前空行').anchor,'# 重复')
assert.equal(lib.changeLocation(source,first,'','标题前空行').line,2)
assert.equal(lib.changeLocation(source,second,'','标题前空行').occurrence,1)
assert.equal(lib.changeLocation(source,second+5,'','标题后空行').line,4)
assert.equal(lib.changeLocation(source,second+5,'','标题后空行').anchor,'# 重复')
assert.equal(lib.styleChangeGroup('headings.0.space_before_pt'),'标题版式')
let state={}, located=[], decisions=[], batches=[]
const Grouped=compile('components/GroupedChanges.tsx',name=>{
  if(name==='react')return {...React,useMemo:fn=>fn(),useEffect:()=>{},useState:initial=>[state,fn=>{state=typeof fn==='function'?fn(state):fn}]}
  if(name==='../lib/changeGrouping')return lib
  if(name==='./RevisionDiff')return {TextDiff:({before,after,side})=>side==='before'?before:after}
  if(name.endsWith('.css'))return {}
  return require(name)
}).default
const props={changes:many,activeId:'',contextKey:'run:1',disabled:false,blockedIds:[],onLocate:x=>located.push(x.id),onDecide:(x,d)=>decisions.push([x.id,d]),onBatch:(x,d)=>batches.push([x.map(y=>y.id),d])}
const visit=(node,predicate)=>!node||typeof node!=='object'?[]:[...(predicate(node)?[node]:[]),...React.Children.toArray(node.props?.children).flatMap(x=>visit(x,predicate))]
const text=node=>typeof node==='string'||typeof node==='number'?String(node):node&&typeof node==='object'?React.Children.toArray(node.props?.children).map(text).join(''):''
const button=(tree,label)=>visit(tree,x=>x.type==='button'&&text(x)===label)[0]
let tree=Grouped(props)
assert.doesNotMatch(renderToStaticMarkup(tree),/<details[^>]+open/)
button(tree,'定位下一处').props.onClick();tree=Grouped(props);button(tree,'定位下一处').props.onClick()
assert.deepEqual(located,['c0','c1'])
button(tree,'接受此组').props.onClick()
assert.equal(batches[0][0].length,130)
assert.equal(batches[0][1],'accept')
tree=Grouped({...props,changes:[change('ref',0,{kind:'reference',rule:'reference.gb7714'})]})
assert.equal(button(tree,'接受此组').props.disabled,true)
assert.equal(button(tree,'保留此组').props.disabled,false)
tree=Grouped({...props,changes:competing})
assert.equal(button(tree,'接受此组').props.disabled,true)
tree=Grouped({...props,changes:duplicate[0].items})
assert.equal(visit(tree,x=>x.type==='article').length,1)
console.log('PASS: 130-item default-collapsed groups, global identical-source merge, conflicts, UTF-16/repeated heading positions, next-location, safe batch and individual controls')
