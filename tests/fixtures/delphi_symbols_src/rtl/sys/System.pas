unit System;

interface

type
  TObject = class
  public
    constructor Create;
    procedure Free;
    function ToString: string; virtual;
  private
    FHidden: Integer;
  end;

procedure WriteLine(const Text: string);
function Sample(A: Integer; B: Integer = 0): Integer;

implementation

procedure Internal;
begin
end;

procedure WriteLine(const Text: string);
begin
  Internal;
end;

function Sample(A: Integer; B: Integer): Integer;
begin
  Result := A + B;
end;

end.
