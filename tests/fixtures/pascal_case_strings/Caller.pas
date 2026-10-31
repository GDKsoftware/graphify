unit Caller;

interface

uses
  Helpers;

procedure Rapport;

implementation

function HaalSQL(const Q: string): string;
begin
  Result := Q;
end;

procedure Rapport;
var
  S: string;
begin
  S := Maandnaam(3);
  S := HaalSQL('select initcap(a) from dual');
end;

end.
