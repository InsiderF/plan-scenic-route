#!/usr/bin/env python3
"""Offline checks for route safety gates, time accounting, and pedestrian rules."""
import copy
import scenic as s


def rejected(fn):
    try:
        fn()
    except s.Insufficient:
        return
    raise AssertionError('Expected explicit insufficient-data failure')


def main():
    source={'url':'https://www.openstreetmap.org/way/10','checked_at':'2026-09-30'}
    raw={'elements':[{'type':'node','id':i,'lon':120+i*.001,'lat':30} for i in [1,2,3,4]]+
         [{'type':'way','id':10,'nodes':[1,2,3],'tags':{'highway':'footway','oneway':'yes'}}]}
    index=s.get_elements(raw);coords,adj=s.graph(index)
    edges,minutes=s.shortest(adj,3,1)
    assert len(edges)==2 and minutes>0 # Vehicle one-way is not foot one-way.
    for highway in ['motorway','construction','proposed']:
        assert not s.walk_allowed({'highway':highway})
    assert not s.walk_allowed({'highway':'path','access':'private'})
    assert not s.walk_allowed({'highway':'path','foot:conditional':'no @ (night)'})
    assert s.walk_allowed({'highway':'path','access':'no','foot':'designated'})
    restricted=copy.deepcopy(raw);restricted['elements'][-1]['tags']['oneway:foot']='yes'
    rejected(lambda:s.shortest(s.graph(s.get_elements(restricted))[1],3,1))
    rejected(lambda:s.shortest(adj,1,4))
    node_gate=copy.deepcopy(raw)
    node_gate['elements'][1]['tags']={'barrier':'gate','foot':'no'}
    rejected(lambda:s.shortest(s.graph(s.get_elements(node_gate))[1],1,3))
    rejected(lambda:s.get_elements({'elements':raw['elements'],'remark':'timeout'}))
    pois={str(i):{'id':str(i),'name':str(i),'access_node':i,'dwell_minutes':20,'source':source,
                  'access_source':source,'access_verified':True,'availability':'unverified'} for i in [1,3]}
    legs,total,_=s.schedule(['1','3'],adj,pois,30)
    assert abs(total-(sum(l['minutes'] for l in legs)+70))<.1
    rejected(lambda:s.verify_poi(pois['1'],coords,adj,index,False))
    assert s.verify_poi(pois['1'],coords,adj,index,True)['coordinate']==coords[1]
    assert not s.source_ok({'url':'https://example.com','checked_at':'未核实'})
    closed={**pois['3'],'availability':'closed'}
    rejected(lambda:s.verify_poi(closed,coords,adj,index,True))
    timed=copy.deepcopy(pois);timed['3']['opening_intervals']=[[540,550]]
    rejected(lambda:s.schedule(['1','3'],adj,timed,30,'09:00'))
    transit={'name':'boat','mode':'boat','from_access_node':1,'to_access_node':3,
             'geometry':[coords[1],coords[3]],'geometry_license':'proprietary','geometry_source':source,
             'service_source':source,'availability':'verified','travel_minutes':5,'wait_minutes':2}
    rejected(lambda:s.add_transit({'transit':[transit]},coords,adj,['walk','boat']))
    transit['geometry_license']='ODbL-1.0'
    s.add_transit({'transit':[transit]},coords,adj,['walk','boat'])
    assert s.shortest(adj,1,3)[1]>0
    payload=s.safe_json({'description':'</script><script>alert(1)</script>'})
    assert '</script>' not in payload
    # North -> east is right; north -> west is left. Same-road bends without a fork stay grouped.
    def edge(a,b,way=10,name='湖岸步道',node=2):
        return {'mode':'walk','geometry':[a,b],'name':name,'way_id':way,'from_node':node,
                'metres':s.distance(a,b),'minutes':1,'bridge':False,'highway':'footway'}
    north=edge([120,30],[120,30.001]);east=edge([120,30.001],[120.001,30.001],11)
    west=edge([120,30.001],[119.999,30.001],11)
    assert s.turn_action(north,east)=='向右转' and s.turn_action(north,west)=='向左转'
    assert len(s.directions([north,east],{2:[(1,{}),(3,{})]},[],'甲','乙'))==1
    ds=s.directions([north,east],{2:[(1,{}),(3,{}),(4,{})]},[],'甲','乙')
    assert len(ds)==2 and ds[1]['action']=='向右转' and ds[1]['end_edge']==2
    assert '到达乙' in ds[-1]['instruction']
    unnamed=edge([120,30],[120,30.001],name='步道');unnamed['bridge']=True;unnamed['highway']='steps'
    text=s.directions([unnamed],{},[],'甲','乙')[0]['instruction']
    assert all(t in text for t in ['未标道路名','桥梁','台阶'])
    print('Passed: foot restrictions, disconnected roads, incomplete extracts, availability, time budget, transport license, and safe embedding.')


if __name__=='__main__':
    main()
