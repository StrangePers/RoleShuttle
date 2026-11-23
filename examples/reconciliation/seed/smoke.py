from importer import reconcile

assert reconcile([], []) == {}
assert reconcile([dict(id="7", seq=1, state="paid", total="1.25")],
                 [dict(ref="7",eid="a",kind="capture",minor=83)]) == {"north/7":125, "coast/7":83}
assert reconcile([], [dict(ref="y", eid="n", kind="memo")]) == {}
print("3 public checks passed")
