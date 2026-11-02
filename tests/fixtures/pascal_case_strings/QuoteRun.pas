unit QuoteRun;

interface

procedure Bouw;
procedure Verder;

implementation

function Plak(const S: string): string;
begin
  Result := S;
end;

procedure Bouw;
var
  A, B: string;
begin
  A := Plak('f(''' + A + ''',' + B + ')');
  Verder;
end;

procedure Verder;
begin
  Plak('x');
end;

end.
