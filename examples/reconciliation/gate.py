"""External held-out acceptance checks. Use copies of the fixture."""
import copy
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys

def n(seq, state="paid", total="1.00", ident="a", **metadata):
    return dict(id=ident, seq=seq, state=state, total=total, **metadata)

def c(eid, kind="capture", minor=10, ident="a"):
    values=dict(ref=ident, eid=eid, kind=kind)
    if minor is not None: values["minor"]=minor
    return values

cases = [
 ("empty", [], [], {}),
 ("decimal", [n(1,total="1.15")], [], {"north/a":115}),
 ("large exact", [n(1,total="90071992547409.91")], [], {"north/a":9007199254740991}),
 ("zero paid", [n(0,total="0")], [], {"north/a":0}),
 ("refund supersedes", [n(2,"refunded","7.01"),n(1)], [], {"north/a":-701}),
 ("cancel supersedes", [n(1),n(3,"cancelled"),n(2)], [], {}),
 ("pending supersedes", [n(9,"pending"),n(8)], [], {}),
 ("highest conflict", [n(3),n(3,total="2.00"),n(1)], [], {}),
 ("state conflict", [n(3),n(3,"refunded")], [], {}),
 ("resolved conflict", [n(2),n(2,"pending"),n(3)], [], {"north/a":100}),
 ("numeric equality", [n(3,total="5",seen="x"),n(3,total="5.00",seen="y")], [], {"north/a":500}),
 ("opaque ids", [n(1,ident="001"),n(1,ident="1",total="9")], [], {"north/001":100,"north/1":900}),
 ("refund only", [], [c("x","refund",11)], {"coast/a":-11}),
 ("duplicate", [], [c("x"),c("x")], {"coast/a":10}),
 ("separate ref", [], [c("x"),c("x",ident="b")], {"coast/a":10,"coast/b":10}),
 ("conflict amount", [], [c("x"),c("x",minor=11),c("y",minor=4)], {"coast/a":4}),
 ("all conflict", [], [c("x"),c("x","refund")], {}),
 ("memo reuse", [], [c("x","memo",None),c("x")], {"coast/a":10}),
 ("zero net", [], [c("x"),c("y","refund")], {"coast/a":0}),
 ("zero event", [], [c("x",minor=0)], {"coast/a":0}),
 ("cross supplier", [n(1)], [c("k")], {"coast/a":10,"north/a":100}),
]
def main():
    sys.path.insert(0,sys.argv[1])
    from importer import reconcile
    fails=[]
    for label,north,coast,answer in cases:
        for a,b in itertools.product((north,list(reversed(north))), (coast,list(reversed(coast)))):
            inputs=copy.deepcopy((a,b))
            try:
                result=reconcile(*inputs)
                assert type(result) is dict and all(type(v) is int for v in result.values())
                assert result==answer and inputs==(a,b)
            except Exception:
                fails.append(label)
                break
    print(json.dumps({"total":len(cases),"passed":len(cases)-len(fails),"failed":fails}))
    sys.exit(bool(fails))
if __name__=="__main__": main()
