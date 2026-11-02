unit ChildLaad;

interface

uses
  BaseLaadB;

type
  TChildLaad = class(TBaseLaadB)
  public
    procedure Doe;
  end;

implementation

procedure TChildLaad.Doe;
begin
  Laad;
end;

end.
