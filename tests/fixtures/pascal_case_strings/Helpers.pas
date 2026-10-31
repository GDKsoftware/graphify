unit Helpers;

interface

function Maandnaam(Nr: Integer): string;
function InitCap(const S: string): string;

implementation

function Maandnaam(Nr: Integer): string;
begin
  Result := 'januari';
end;

function InitCap(const S: string): string;
begin
  Result := S;
end;

end.
