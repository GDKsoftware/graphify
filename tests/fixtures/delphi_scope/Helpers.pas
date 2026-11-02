unit Helpers;

interface

function InitCap(const S: string): string;

implementation

function Intern: Integer;
begin
  Result := 1;
end;

function InitCap(const S: string): string;
begin
  Intern;
  Result := S;
end;

end.
